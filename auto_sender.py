import argparse
import asyncio
import csv
import sys
import threading
import os
import random
import time
from pathlib import Path

import pyperclip
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon import events
from telethon.errors import FloodWaitError

# --- Cargar config ---
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE = os.getenv("TELEGRAM_PHONE", "")
DESTINO = os.getenv("TELEGRAM_DESTINO", "ZephyrChkV3Bot")
PREFIJO_DEFAULT = os.getenv("TELEGRAM_PREFIJO", ".zo")
INTERVALO_MIN = float(os.getenv("TELEGRAM_INTERVALO_MIN", os.getenv("TELEGRAM_INTERVALO", "8.0")))
INTERVALO_MAX = float(os.getenv("TELEGRAM_INTERVALO_MAX", str(max(INTERVALO_MIN, 12.0))))
INTERVALO_ALT = float(os.getenv("TELEGRAM_INTERVALO_ALT", "30.0"))
MESSAGE_BATCH = int(os.getenv("TELEGRAM_MESSAGE_BATCH", "7"))
ANTISPAM_COOLDOWN = float(os.getenv("TELEGRAM_ANTISPAM_COOLDOWN", "90.0"))
RESPUESTA_ESPERA = float(os.getenv("TELEGRAM_RESPUESTA_ESPERA", "1.5"))
LOG_FILE = os.getenv("TELEGRAM_LOG_FILE", "telegram_antispam_log.csv")
ADAPTIVE_INCREMENTO = float(os.getenv("TELEGRAM_ADAPTIVE_INCREMENTO", "0.6"))
ADAPTIVE_EXTRA_MAX = float(os.getenv("TELEGRAM_ADAPTIVE_EXTRA_MAX", "3.0"))
ADAPTIVE_RECUPERACION_CADA = int(os.getenv("TELEGRAM_ADAPTIVE_RECUPERACION_CADA", "5"))
ADAPTIVE_RECUPERACION = float(os.getenv("TELEGRAM_ADAPTIVE_RECUPERACION", "0.2"))
LOG_PATH = Path(LOG_FILE)
if not LOG_PATH.is_absolute():
    LOG_PATH = Path(__file__).parent / LOG_PATH
LOG_COLUMNS = (
    "timestamp",
    "batch_id",
    "message_num",
    "total_messages",
    "attempt",
    "result",
    "batch_elapsed_s",
    "since_previous_send_s",
    "target_interval_s",
    "adaptive_extra_s",
    "cooldown_s",
    "flood_wait_s",
)

if INTERVALO_MAX < INTERVALO_MIN:
    INTERVALO_MAX = INTERVALO_MIN

# --- Argumentos de línea de comandos ---
parser = argparse.ArgumentParser(description="Auto-sender de mensajes a Telegram")
parser.add_argument("--prefijo", default=PREFIJO_DEFAULT, help=f"Prefijo a usar (default: {PREFIJO_DEFAULT})")
args = parser.parse_args()

PREFIJO = args.prefijo

if not API_ID or not API_HASH:
    print("[!] Falta TELEGRAM_API_ID o TELEGRAM_API_HASH en el archivo .env")
    print("    Obtenelos en https://my.telegram.org/apps")
    sys.exit(1)

if not PHONE:
    print("[!] Falta TELEGRAM_PHONE en el archivo .env")
    print("    Agrega tu numero con codigo de pais, ej: +521234567890")
    sys.exit(1)

print(f"[*] Destino: @{DESTINO}")
print(f"[*] Prefijo: {PREFIJO}")
print(f"[*] Intervalo real entre envios: {INTERVALO_MIN}-{INTERVALO_MAX}s (cada {MESSAGE_BATCH} mensajes: {INTERVALO_ALT}s)")
print(f"[*] Cooldown antispam: {ANTISPAM_COOLDOWN}s")
print("[*] Modo adaptativo: sube el intervalo si detecta antispam")
print(f"[*] Log sin contenido: {LOG_PATH}")
print()

# --- Estado ---
ultimo_portapapeles = ""
enviando = False
lock = threading.Lock()
antispam_hasta = 0.0
antispam_version = 0


def formato_segundos(valor):
    if valor is None:
        return ""
    return f"{valor:.3f}"


def registrar_log(
    batch_id,
    message_num,
    total_messages,
    attempt,
    result,
    batch_elapsed,
    since_previous_send=None,
    target_interval=None,
    adaptive_extra=0.0,
    cooldown=None,
    flood_wait=None,
):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    necesita_header = not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0

    with LOG_PATH.open("a", newline="", encoding="utf-8") as archivo:
        writer = csv.DictWriter(archivo, fieldnames=LOG_COLUMNS)
        if necesita_header:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "batch_id": batch_id,
                "message_num": message_num,
                "total_messages": total_messages,
                "attempt": attempt,
                "result": result,
                "batch_elapsed_s": formato_segundos(batch_elapsed),
                "since_previous_send_s": formato_segundos(since_previous_send),
                "target_interval_s": formato_segundos(target_interval),
                "adaptive_extra_s": formato_segundos(adaptive_extra),
                "cooldown_s": formato_segundos(cooldown),
                "flood_wait_s": formato_segundos(flood_wait),
            }
        )


def detectar_antispam(texto):
    texto = (texto or "").lower()
    patrones = (
        "antispam",
        "anti spam",
        "avoid sending repeated requests",
        "repeated requests",
        "flood",
    )
    return any(patron in texto for patron in patrones)


async def esperar_si_antispam():
    restante = antispam_hasta - time.monotonic()
    if restante > 0:
        print(f"    [!] Antispam activo. Pausando {restante:.0f}s...")
        await asyncio.sleep(restante)


def intervalo_objetivo(indice_anterior, extra_adaptativo):
    if MESSAGE_BATCH > 0 and indice_anterior > 0 and indice_anterior % MESSAGE_BATCH == 0:
        return INTERVALO_ALT
    return random.uniform(INTERVALO_MIN + extra_adaptativo, INTERVALO_MAX + extra_adaptativo)


async def esperar_intervalo_total(ultimo_envio, intervalo):
    if not ultimo_envio or intervalo <= 0:
        return

    restante = (ultimo_envio + intervalo) - time.monotonic()
    if restante > 0:
        await asyncio.sleep(restante)


def agregar_prefijo(texto):
    """Agrega el prefijo a cada linea si no lo tiene ya."""
    lineas = texto.strip().split("\n")
    resultado = []
    vistos = set()
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        if linea.startswith(PREFIJO + " "):
            mensaje = linea
        else:
            mensaje = f"{PREFIJO} {linea}"
        if mensaje not in vistos:
            resultado.append(mensaje)
            vistos.add(mensaje)
    return resultado


async def main():
    global ultimo_portapapeles, enviando

    client = TelegramClient("anon", API_ID, API_HASH)

    print("[*] Conectando a Telegram...")
    await client.start(phone=PHONE)
    print(f"[*] Conectado como: {await client.get_me()}")
    print()

    # Resolver destino
    try:
        destino = await client.get_input_entity(DESTINO)
    except Exception as e:
        print(f"[!] No se encontro @{DESTINO}: {e}")
        print("    Abri Telegram, busca a @{DESTINO} e inicia una conversacion, despues volve a ejecutar.")
        await client.disconnect()
        sys.exit(1)

    print(f"[*] Destino resuelto: {DESTINO}")
    print("[*] Monitoreando portapapeles... Copia mensajes de Portapapeles.")
    print("[*] Ctrl+C para salir.")
    print()

    @client.on(events.NewMessage(chats=destino))
    async def on_respuesta(event):
        global antispam_hasta, antispam_version
        if event.out:
            return
        texto = event.raw_text or ""
        if detectar_antispam(texto):
            antispam_version += 1
            antispam_hasta = max(antispam_hasta, time.monotonic() + ANTISPAM_COOLDOWN)
            print(f"    [!] Respuesta antispam detectada. Enfriando {ANTISPAM_COOLDOWN:.0f}s.")

    while True:
        try:
            actual = pyperclip.paste()

            if actual and actual != ultimo_portapapeles and not enviando:
                lineas = agregar_prefijo(actual)

                with lock:
                    enviando = True

                print(f"\n[*] Detectadas {len(lineas)} mensajes. Enviando...")

                batch_id = time.strftime("%Y%m%d-%H%M%S")
                batch_inicio = time.monotonic()
                ultimo_envio = 0.0
                extra_adaptativo = 0.0
                limpios_seguidos = 0

                for i, linea in enumerate(lineas, 1):
                    intento = 1
                    while True:
                        intervalo = None
                        try:
                            await esperar_si_antispam()
                            intervalo = 0.0 if intento > 1 else intervalo_objetivo(i - 1, extra_adaptativo)
                            await esperar_intervalo_total(ultimo_envio, intervalo)
                            antispam_antes = antispam_version
                            envio_anterior = ultimo_envio

                            await client.send_message(destino, linea)
                            ultimo_envio = time.monotonic()
                            extra = f" (reintento {intento})" if intento > 1 else ""
                            since_previous_send = (
                                ultimo_envio - envio_anterior if envio_anterior else None
                            )
                            print(f"    {i}/{len(lineas)}{extra} enviado")

                            await asyncio.sleep(RESPUESTA_ESPERA)

                            if antispam_version != antispam_antes:
                                registrar_log(
                                    batch_id=batch_id,
                                    message_num=i,
                                    total_messages=len(lineas),
                                    attempt=intento,
                                    result="antispam",
                                    batch_elapsed=time.monotonic() - batch_inicio,
                                    since_previous_send=since_previous_send,
                                    target_interval=intervalo,
                                    adaptive_extra=extra_adaptativo,
                                    cooldown=ANTISPAM_COOLDOWN,
                                )
                                extra_adaptativo = min(
                                    ADAPTIVE_EXTRA_MAX,
                                    extra_adaptativo + ADAPTIVE_INCREMENTO,
                                )
                                limpios_seguidos = 0
                                print(
                                    f"    [!] Antispam en {i}/{len(lineas)}. "
                                    f"Reintentando la misma linea en modo +{extra_adaptativo:.1f}s..."
                                )
                                intento += 1
                                continue

                            registrar_log(
                                batch_id=batch_id,
                                message_num=i,
                                total_messages=len(lineas),
                                attempt=intento,
                                result="ok",
                                batch_elapsed=time.monotonic() - batch_inicio,
                                since_previous_send=since_previous_send,
                                target_interval=intervalo,
                                adaptive_extra=extra_adaptativo,
                            )
                            limpios_seguidos += 1
                            if extra_adaptativo > 0 and limpios_seguidos >= ADAPTIVE_RECUPERACION_CADA:
                                extra_adaptativo = max(
                                    0.0,
                                    extra_adaptativo - ADAPTIVE_RECUPERACION,
                                )
                                limpios_seguidos = 0
                                if extra_adaptativo:
                                    print(f"    [*] Ritmo adaptativo bajando a +{extra_adaptativo:.1f}s.")
                                else:
                                    print("    [*] Ritmo base restaurado.")

                            break
                        except FloodWaitError as e:
                            registrar_log(
                                batch_id=batch_id,
                                message_num=i,
                                total_messages=len(lineas),
                                attempt=intento,
                                result="flood_wait",
                                batch_elapsed=time.monotonic() - batch_inicio,
                                target_interval=intervalo,
                                adaptive_extra=extra_adaptativo,
                                flood_wait=e.seconds,
                            )
                            extra_adaptativo = min(
                                ADAPTIVE_EXTRA_MAX,
                                extra_adaptativo + ADAPTIVE_INCREMENTO,
                            )
                            limpios_seguidos = 0
                            print(f"    [!] Flood wait {e.seconds}s, esperando para reintentar la misma linea...")
                            await asyncio.sleep(e.seconds)
                            intento += 1

                print(f"[*] Lote completado. Seguimos monitoreando...")
                ultimo_portapapeles = actual

                with lock:
                    enviando = False

            await asyncio.sleep(0.5)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[!] Error: {e}")
            await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Chau.")
