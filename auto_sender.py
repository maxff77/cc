import argparse
import asyncio
import csv
import sys
import threading
import os
import time
from pathlib import Path
import re

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
DESTINOS_RAW = [d.strip() for d in os.getenv("TELEGRAM_DESTINO", "ZephyrChkV3Bot").split(",") if d.strip()]
PREFIJO_DEFAULT = os.getenv("TELEGRAM_PREFIJO", ".zo")
INTERVALO = float(os.getenv("TELEGRAM_INTERVALO", "8.0"))
ANTISPAM_COOLDOWN = float(os.getenv("TELEGRAM_ANTISPAM_COOLDOWN", "90.0"))
RESPUESTA_ESPERA = float(os.getenv("TELEGRAM_RESPUESTA_ESPERA", "1.5"))
LOG_FILE = os.getenv("TELEGRAM_LOG_FILE", "telegram_antispam_log.csv")
ADAPTIVE_INCREMENTO = float(os.getenv("TELEGRAM_ADAPTIVE_INCREMENTO", "0.6"))
ADAPTIVE_EXTRA_MAX = float(os.getenv("TELEGRAM_ADAPTIVE_EXTRA_MAX", "3.0"))
ADAPTIVE_RECUPERACION_CADA = int(os.getenv("TELEGRAM_ADAPTIVE_RECUPERACION_CADA", "5"))
ADAPTIVE_RECUPERACION = float(os.getenv("TELEGRAM_ADAPTIVE_RECUPERACION", "0.2"))
ANTISPAM_KEYWORDS_RAW = os.getenv("TELEGRAM_ANTISPAM_KEYWORDS", "antispam,anti spam,avoid sending repeated requests,repeated requests,flood")
ANTISPAM_KEYWORDS = tuple(kw.strip().lower() for kw in ANTISPAM_KEYWORDS_RAW.split(",") if kw.strip())
LOG_MAX_SIZE_MB = float(os.getenv("TELEGRAM_LOG_MAX_SIZE_MB", "10"))
LOG_MAX_FILES = int(os.getenv("TELEGRAM_LOG_MAX_FILES", "5"))
TELEGRAM_RESPUESTAS_FILE = os.getenv("TELEGRAM_RESPUESTAS_FILE", "")
TIEMPO_RESPUESTA = float(os.getenv("TELEGRAM_TIEMPO_RESPUESTA", "30"))
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

# --- Argumentos de línea de comandos ---
parser = argparse.ArgumentParser(description="Auto-sender de mensajes a Telegram")
parser.add_argument("--prefijo", default=PREFIJO_DEFAULT, help=f"Prefijo a usar (default: {PREFIJO_DEFAULT})")
parser.add_argument("--dry-run", action="store_true", help="Simula el envío sin mandar mensajes reales a Telegram")
args = parser.parse_args()

PREFIJO = args.prefijo
DRY_RUN = args.dry_run

_prefijo_slug = PREFIJO.lstrip(".").replace(" ", "_") or "sin_prefijo"
# Prefijo de orden inverso: la sesion mas reciente queda primera al ordenar por nombre.
_orden_inverso = 9999999999 - int(time.time())
_sesion_nombre = f"{_orden_inverso}_{time.strftime('%Y%m%d-%H%M%S')}"
SESION_RESPUESTAS_DIR = (
    Path(__file__).parent / "respuestas" / _prefijo_slug / _sesion_nombre
    if TELEGRAM_RESPUESTAS_FILE else None
)

if not API_ID or not API_HASH:
    print("[!] Falta TELEGRAM_API_ID o TELEGRAM_API_HASH en el archivo .env")
    print("    Obtenelos en https://my.telegram.org/apps")
    sys.exit(1)

if not PHONE:
    print("[!] Falta TELEGRAM_PHONE en el archivo .env")
    print("    Agrega tu numero con codigo de pais, ej: +521234567890")
    sys.exit(1)

print(f"[*] Destinos: {', '.join('@' + d for d in DESTINOS_RAW)}")
print(f"[*] Prefijo: {PREFIJO}")
print(f"[*] Intervalo entre envios: {INTERVALO}s")
print(f"[*] Cooldown antispam: {ANTISPAM_COOLDOWN}s")
print("[*] Modo adaptativo: sube el intervalo si detecta antispam")
if DRY_RUN:
    print("[*] ⚠ MODO SIMULACION (--dry-run): no se enviaran mensajes reales")
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


def rotar_log_si_necesario():
    """Renombra el log actual si excede LOG_MAX_SIZE_MB y limpia archivos viejos."""
    if not LOG_PATH.exists():
        return
    max_bytes = LOG_MAX_SIZE_MB * 1024 * 1024
    if LOG_PATH.stat().st_size < max_bytes:
        return
    # Rotar: renombrar actual con timestamp
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rotado = LOG_PATH.with_name(f"{LOG_PATH.stem}.{stamp}{LOG_PATH.suffix}")
    LOG_PATH.rename(rotado)
    # Limpiar archivos viejos
    existentes = sorted(
        LOG_PATH.parent.glob(f"{LOG_PATH.stem}.*{LOG_PATH.suffix}"),
        key=lambda p: p.stat().st_mtime,
    )
    while len(existentes) > LOG_MAX_FILES:
        existentes.pop(0).unlink()


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
    rotar_log_si_necesario()
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
    return any(patron in texto for patron in ANTISPAM_KEYWORDS)


def extraer_cooldown_mensaje(texto):
    """Extrae los segundos de cooldown de un mensaje antispam tipo '(4\'s)', '(10s)', '(4 s)'."""
    if not texto:
        return None
    match = re.search(r"\((\d+)\s*'?s\)", texto, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


RE_CC = re.compile(r"(?i)\bCC\s*:\s*([^\n]+)")


def guardar_respuesta(texto):
    if not SESION_RESPUESTAS_DIR or "✅" not in texto:
        return
    SESION_RESPUESTAS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y-%m-%d %H:%M:%S')

    # Archivo completo (como antes) + linea en blanco entre cada respuesta
    with (SESION_RESPUESTAS_DIR / "completa.txt").open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {texto}\n\n")

    # Archivo filtrado: solo el dato despues de CC:, limpio, una por linea
    datos = []
    for m in RE_CC.findall(texto):
        dato = m.split("Status")[0].strip()
        if dato:
            datos.append(dato)
    if datos:
        with (SESION_RESPUESTAS_DIR / "filtrada.txt").open("a", encoding="utf-8") as f:
            f.write("\n".join(datos) + "\n")


async def esperar_si_antispam():
    restante = antispam_hasta - time.monotonic()
    if restante > 0:
        print(f"    [!] Antispam activo. Pausando {restante:.0f}s...")
        await asyncio.sleep(restante)


async def esperar_si_pausado():
    """Espera activa si existe el archivo centinela .pause en el directorio del proyecto."""
    pause_file = Path(__file__).parent / ".pause"
    while pause_file.exists():
        print("    [⏸] PAUSADO. Elimina el archivo .pause para continuar...")
        await asyncio.sleep(1)


def intervalo_objetivo(extra_adaptativo, umbral_precautorio=0, indice_anterior=0):
    extra_precautorio = 0.0
    if umbral_precautorio > 0 and indice_anterior >= int(umbral_precautorio * 0.7):
        if indice_anterior >= int(umbral_precautorio * 0.85):
            extra_precautorio = 0.6
        else:
            extra_precautorio = 0.3
    return INTERVALO + extra_adaptativo + extra_precautorio


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


def formatear_progreso(actual, total, inicio, extra_adaptativo):
    """Devuelve una línea de progreso con barra, porcentaje y ETA."""
    if total <= 0:
        return f"    {actual}/? enviado"
    pct = actual / total
    elapsed = time.monotonic() - inicio
    if actual > 1 and pct > 0:
        eta = (elapsed / actual) * (total - actual)
        eta_str = f"{int(eta // 60)}m {int(eta % 60)}s"
    else:
        eta_str = "calculando..."
    bar_width = 20
    filled = int(bar_width * pct)
    if filled < bar_width:
        bar = "=" * filled + ">" + " " * (bar_width - filled - 1)
    else:
        bar = "=" * bar_width
    ritmo = f"+{extra_adaptativo:.1f}s" if extra_adaptativo > 0 else "base"
    return f"\r    [{bar}] {int(pct*100):3d}% ({actual}/{total}) | ETA: {eta_str} | Ritmo: {ritmo}"


async def main():
    global ultimo_portapapeles, enviando

    client = TelegramClient("anon", API_ID, API_HASH)

    print("[*] Conectando a Telegram...")
    await client.start(phone=PHONE)
    print(f"[*] Conectado como: {await client.get_me()}")
    print()

    # Resolver destinos
    destinos = []
    for _d in DESTINOS_RAW:
        try:
            destinos.append(await client.get_input_entity(_d))
        except Exception as e:
            print(f"[!] No se encontro @{_d}: {e}")
            print(f"    Abri Telegram, busca a @{_d} e inicia una conversacion, despues volve a ejecutar.")
            await client.disconnect()
            sys.exit(1)

    print(f"[*] {len(destinos)} destino(s) resuelto(s): {', '.join('@' + d for d in DESTINOS_RAW)}")
    print("[*] Monitoreando portapapeles... Copia mensajes de Portapapeles.")
    print("[*] Ctrl+C para salir.")
    print()

    respuesta_event = asyncio.Event()
    respuestas_guardadas = set()   # ids de mensajes ya guardados (dedup)
    respuestas_recibidas = 0       # nº de ✅ guardados (sesión)
    respuestas_rechazadas = 0      # nº de ❌ u otras respuestas (sesión)
    enviados_total = 0             # nº de líneas .zo enviadas (sesión)

    async def _manejar_bot(texto, message_id=None, fuente=""):
        global antispam_hasta, antispam_version
        nonlocal respuestas_recibidas, respuestas_rechazadas
        if detectar_antispam(texto):
            cooldown_extraido = extraer_cooldown_mensaje(texto)
            cooldown = cooldown_extraido if cooldown_extraido and cooldown_extraido > ANTISPAM_COOLDOWN else ANTISPAM_COOLDOWN
            antispam_version += 1
            antispam_hasta = max(antispam_hasta, time.monotonic() + cooldown)
            respuesta_event.set()
            print(f"    [!] Antispam{fuente}. Enfriando {cooldown:.0f}s.")
        elif "✅" in texto and message_id not in respuestas_guardadas:
            respuestas_guardadas.add(message_id)
            respuesta_event.set()
            guardar_respuesta(texto)
            respuestas_recibidas += 1
            pendientes = max(0, enviados_total - respuestas_recibidas - respuestas_rechazadas)
            print(f"\n    [✓] Respuesta {respuestas_recibidas} guardada{fuente}. ⏳ {pendientes} esperando.")
        elif "❌" in texto and message_id not in respuestas_guardadas:
            respuestas_guardadas.add(message_id)
            respuesta_event.set()
            respuestas_rechazadas += 1
            pendientes = max(0, enviados_total - respuestas_recibidas - respuestas_rechazadas)
            print(f"\n    [✗] Rechazada {respuestas_rechazadas}{fuente}. ⏳ {pendientes} esperando.")

    @client.on(events.NewMessage(chats=destinos))
    async def on_respuesta(event):
        if event.out:
            return
        await _manejar_bot(event.raw_text or "", event.message.id)

    @client.on(events.MessageEdited(chats=destinos))
    async def on_respuesta_editada(event):
        if event.out:
            return
        await _manejar_bot(event.raw_text or "", event.message.id, " (edit)")

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
                contador_antispam_lote = 0
                umbral_antispam_historico = 0

                for i, linea in enumerate(lineas, 1):
                    intento = 1
                    while True:
                        intervalo = None
                        try:
                            await esperar_si_pausado()
                            await esperar_si_antispam()
                            intervalo = 0.0 if intento > 1 else intervalo_objetivo(extra_adaptativo, umbral_antispam_historico, i - 1)
                            await esperar_intervalo_total(ultimo_envio, intervalo)
                            antispam_antes = antispam_version
                            envio_anterior = ultimo_envio

                            if DRY_RUN:
                                await asyncio.sleep(0.05)
                            else:
                                destino_actual = destinos[(i - 1) % len(destinos)]
                                await client.send_message(destino_actual, linea)
                            ultimo_envio = time.monotonic()
                            extra = f" (reintento {intento})" if intento > 1 else ""
                            since_previous_send = (
                                ultimo_envio - envio_anterior if envio_anterior else None
                            )
                            if extra:
                                print(f"    {i}/{len(lineas)}{extra} enviado")
                            else:
                                print(formatear_progreso(i, len(lineas), batch_inicio, extra_adaptativo), end="", flush=True)

                            try:
                                await asyncio.wait_for(respuesta_event.wait(), timeout=RESPUESTA_ESPERA)
                            except asyncio.TimeoutError:
                                pass
                            respuesta_event.clear()

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
                                contador_antispam_lote += 1
                                umbral_antispam_historico = max(umbral_antispam_historico, i)
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
                            recuperacion_umbral = ADAPTIVE_RECUPERACION_CADA * (1 + contador_antispam_lote * 2)
                            if extra_adaptativo > 0 and limpios_seguidos >= recuperacion_umbral:
                                floor = min(0.6, contador_antispam_lote * 0.2)
                                extra_adaptativo = max(
                                    floor,
                                    extra_adaptativo - ADAPTIVE_RECUPERACION,
                                )
                                limpios_seguidos = 0
                                if extra_adaptativo:
                                    print(f"    [*] Ritmo adaptativo bajando a +{extra_adaptativo:.1f}s.")
                                else:
                                    print("    [*] Ritmo base restaurado.")

                            enviados_total += 1
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

                pendientes = max(0, enviados_total - respuestas_recibidas - respuestas_rechazadas)
                print(f"\n[*] Lote: {enviados_total} enviados | ✅ {respuestas_recibidas} ok | ❌ {respuestas_rechazadas} rechazadas | ⏳ {pendientes} esperando")
                if pendientes > 0:
                    print(f"[*] Lote completado. El script sigue escuchando; esperando {pendientes} respuestas...")
                else:
                    print("[*] Lote completado. Todas las respuestas recibidas. Seguimos monitoreando...")
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
