"""CLI legacy: monitorea el portapapeles y envía cada línea a Telegram.

La lógica compartida (prefijo, guardado de respuestas, intervalos) vive en
core.py, que también usa la UI web (app.py). Para la interfaz, ejecutá:

    python app.py
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import pyperclip
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

import core

# --- Argumentos de línea de comandos ---
parser = argparse.ArgumentParser(description="Auto-sender de mensajes a Telegram")
parser.add_argument("prefijo", help="Prefijo para cada línea, ej: .zo")
args = parser.parse_args()

PREFIJO = args.prefijo

if not core.API_ID or not core.API_HASH:
    print("[!] Falta TELEGRAM_API_ID o TELEGRAM_API_HASH en el archivo .env")
    print("    Obtenelos en https://my.telegram.org/apps")
    sys.exit(1)

if not core.PHONE:
    print("[!] Falta TELEGRAM_PHONE en el archivo .env")
    print("    Agrega tu numero con codigo de pais, ej: +521234567890")
    sys.exit(1)

if not core.DESTINOS_DEFAULT:
    print("[!] Falta TELEGRAM_DESTINO en el archivo .env")
    sys.exit(1)

SESION = core.Sesion(PREFIJO)

print(f"[*] Destinos: {', '.join('@' + d for d in core.DESTINOS_DEFAULT)}")
print(f"[*] Prefijo: {PREFIJO}")
print(f"[*] Intervalo entre envios: {core.INTERVALO_DEFAULT}s")
print(f"[*] Respuestas en: {SESION.dir}")
print()


async def main():
    # catch_up=True: al reconectar tras una caida, Telethon repesca los mensajes
    # que llegaron mientras estaba desconectado (antes se perdian).
    client = TelegramClient("anon", core.API_ID, core.API_HASH, catch_up=True)

    print("[*] Conectando a Telegram...")
    await client.start(phone=core.PHONE)
    print(f"[*] Conectado como: {await client.get_me()}")
    print()

    destinos = []
    for _d in core.DESTINOS_DEFAULT:
        try:
            destinos.append(await client.get_input_entity(_d))
        except Exception as e:
            print(f"[!] No se encontro @{_d}: {e}")
            print(f"    Abri Telegram, busca a @{_d} e inicia una conversacion, despues volve a ejecutar.")
            await client.disconnect()
            sys.exit(1)

    print(f"[*] {len(destinos)} destino(s) resuelto(s): {', '.join('@' + d for d in core.DESTINOS_DEFAULT)}")
    print("[*] Monitoreando portapapeles... Copia mensajes al portapapeles.")
    print("[*] Ctrl+C para salir.")
    print()

    # message_id -> {"texto": ultimo texto visto, "estado": "ok" | "rechazada" | None}
    estado_mensajes = {}
    respuestas_recibidas = 0
    respuestas_rechazadas = 0
    enviados_total = 0

    def pendientes():
        return max(0, enviados_total - respuestas_recibidas - respuestas_rechazadas)

    async def _manejar_bot(texto, message_id, fuente=""):
        nonlocal respuestas_recibidas, respuestas_rechazadas
        previo = estado_mensajes.get(message_id)
        if previo and previo["texto"] == texto:
            return  # edicion sin cambios reales

        estado_previo = previo["estado"] if previo else None
        if "✅" in texto:
            estado_nuevo = "ok"
        elif "❌" in texto:
            estado_nuevo = "rechazada"
        else:
            estado_nuevo = estado_previo  # edicion intermedia (ej: ⏳), conserva estado
        estado_mensajes[message_id] = {"texto": texto, "estado": estado_nuevo}

        if estado_nuevo == "ok":
            nuevos = SESION.guardar_respuesta(texto)
            if estado_previo != "ok":
                respuestas_recibidas += 1
                if estado_previo == "rechazada":
                    respuestas_rechazadas -= 1
                print(f"\n    [✓] Respuesta {respuestas_recibidas} guardada{fuente}. ⏳ {pendientes()} esperando.")
            elif nuevos:
                print(f"\n    [✓] Edicion con {len(nuevos)} dato(s) nuevo(s) guardada{fuente}.")
        elif estado_nuevo == "rechazada" and estado_previo != "rechazada":
            respuestas_rechazadas += 1
            if estado_previo == "ok":
                respuestas_recibidas -= 1
            print(f"\n    [✗] Rechazada {respuestas_rechazadas}{fuente}. ⏳ {pendientes()} esperando.")

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

    ultimo_portapapeles = ""

    while True:
        try:
            actual = pyperclip.paste()

            if actual and actual != ultimo_portapapeles:
                lineas = core.agregar_prefijo(actual, PREFIJO)

                print(f"\n[*] Detectadas {len(lineas)} mensajes. Enviando...")

                batch_inicio = time.monotonic()
                ultimo_envio = 0.0

                for i, linea in enumerate(lineas, 1):
                    while True:
                        try:
                            await core.esperar_intervalo(ultimo_envio, core.INTERVALO_DEFAULT)
                            destino_actual = destinos[(i - 1) % len(destinos)]
                            await client.send_message(destino_actual, linea)
                            ultimo_envio = time.monotonic()
                            enviados_total += 1
                            print(core.formatear_progreso(i, len(lineas), batch_inicio), end="", flush=True)
                            break
                        except FloodWaitError as e:
                            print(f"\n    [!] Flood wait {e.seconds}s, esperando para reintentar la misma linea...")
                            await asyncio.sleep(e.seconds)

                print(f"\n[*] Lote: {enviados_total} enviados | ✅ {respuestas_recibidas} ok | ❌ {respuestas_rechazadas} rechazadas | ⏳ {pendientes()} esperando")
                if pendientes() > 0:
                    print(f"[*] Lote completado. El script sigue escuchando; esperando {pendientes()} respuestas...")
                else:
                    print("[*] Lote completado. Todas las respuestas recibidas. Seguimos monitoreando...")
                ultimo_portapapeles = actual

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
