"""UI web para el sender de Telegram.

Reemplaza el monitoreo de portapapeles por una interfaz: pegás el texto, lo ves
salir línea por línea, con panel de respuestas en vivo (completa + filtrada),
historial navegable por prefijo/sesión y controles de pausar/reanudar/detener.

Un único cliente Telethon vive en el event loop de uvicorn. El WebSocket empuja
eventos en vivo; REST sirve comandos y el historial de archivos.

Ejecutar:  python app.py   (abre http://127.0.0.1:8000)
"""

import asyncio
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, SessionPasswordNeededError

import core

ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"
HOST = "127.0.0.1"
PORT = 8000


# --------------------------------------------------------------------------- #
# Broadcaster: empuja eventos a todas las pestañas conectadas.
# --------------------------------------------------------------------------- #
class Broadcaster:
    def __init__(self):
        self.conns: set[WebSocket] = set()

    def register(self, ws):
        self.conns.add(ws)

    def unregister(self, ws):
        self.conns.discard(ws)

    async def emit(self, msg: dict):
        for ws in list(self.conns):
            try:
                await ws.send_json(msg)
            except Exception:
                self.conns.discard(ws)


# --------------------------------------------------------------------------- #
# Engine: estado de envío + worker de fondo. Vive fuera del WS para sobrevivir
# reconexiones y broadcastear a todas las pestañas.
# --------------------------------------------------------------------------- #
class Engine:
    def __init__(self):
        self.client: TelegramClient | None = None
        self.bus = Broadcaster()
        self.autorizado = False

        # Cola y parámetros del lote en curso.
        self.cola: list[str] = []
        self.intervalo = core.INTERVALO_DEFAULT
        self.destinos = []          # input entities
        self.destinos_ids = set()   # chat_id para filtrar respuestas
        self.destinos_labels = []   # usernames para mostrar
        self.prefijo_activo = ""
        self.sesion: core.Sesion | None = None

        # Contadores.
        self.enviados_total = 0
        self.respuestas_recibidas = 0
        self.respuestas_rechazadas = 0
        self.estado_mensajes = {}   # message_id -> {"texto", "estado"}

        # Control del worker.
        self.worker_task: asyncio.Task | None = None
        self.pause_event = asyncio.Event()
        self.pause_event.set()      # set = corriendo, clear = en pausa
        self._wake = asyncio.Event()  # despierta sleeps al pausar/detener
        self.stop = False
        self.batch_inicio = 0.0
        self.ultimo_envio = 0.0
        self.estado_envio = "idle"  # idle | enviando | pausado | detenido | completado

    # --- ciclo de vida del cliente ---------------------------------------- #
    async def start_client(self):
        self.client = TelegramClient("anon", core.API_ID, core.API_HASH, catch_up=True)
        await self.client.connect()
        self.autorizado = await self.client.is_user_authorized()
        self._registrar_handlers()

    async def stop_client(self):
        if self.client:
            await self.client.disconnect()

    def _registrar_handlers(self):
        @self.client.on(events.NewMessage())
        async def _on_new(event):
            if event.out or event.chat_id not in self.destinos_ids:
                return
            await self._manejar_bot(event.raw_text or "", event.message.id)

        @self.client.on(events.MessageEdited())
        async def _on_edit(event):
            if event.out or event.chat_id not in self.destinos_ids:
                return
            await self._manejar_bot(event.raw_text or "", event.message.id, " (edit)")

    # --- login (solo si anon.session no está autorizado) ------------------ #
    async def login_send_code(self, phone):
        self._phone = phone
        sent = await self.client.send_code_request(phone)
        self._phone_code_hash = sent.phone_code_hash

    async def login_sign_in(self, code, password=None):
        try:
            await self.client.sign_in(
                self._phone, code, phone_code_hash=self._phone_code_hash
            )
        except SessionPasswordNeededError:
            if not password:
                raise HTTPException(400, "Se requiere contraseña de 2FA")
            await self.client.sign_in(password=password)
        self.autorizado = await self.client.is_user_authorized()
        await self.bus.emit({"tipo": "estado_auth", "autorizado": self.autorizado})

    # --- contadores ------------------------------------------------------- #
    def pendientes(self):
        return max(
            0, self.enviados_total - self.respuestas_recibidas - self.respuestas_rechazadas
        )

    def contadores(self):
        return {
            "enviados": self.enviados_total,
            "ok": self.respuestas_recibidas,
            "rechazadas": self.respuestas_rechazadas,
            "pendientes": self.pendientes(),
            "en_cola": len(self.cola),
        }

    def snapshot(self):
        """Estado completo para una pestaña que recién se conecta."""
        return {
            "tipo": "snapshot",
            "autorizado": self.autorizado,
            "cola": self.cola,
            "contadores": self.contadores(),
            "estado_envio": self.estado_envio,
            "prefijo_activo": self.prefijo_activo,
            "destinos": self.destinos_labels,
            "intervalo": self.intervalo,
            "sesion_activa": self.sesion.info() if self.sesion else None,
        }

    # --- resolución de destinos ------------------------------------------- #
    async def _resolver_destinos(self, destinos_raw):
        ents, ids, labels = [], set(), []
        for d in destinos_raw:
            d = d.strip().lstrip("@")
            if not d:
                continue
            ent = await self.client.get_input_entity(d)
            full = await self.client.get_entity(ent)
            ents.append(ent)
            ids.add(full.id)
            labels.append(d)
        if not ents:
            raise HTTPException(400, "No se especificaron destinos válidos")
        return ents, ids, labels

    # --- iniciar / anexar lote -------------------------------------------- #
    async def iniciar(self, texto, prefijo, destinos_raw, intervalo):
        if not self.autorizado:
            raise HTTPException(400, "Telegram no está autorizado todavía")

        lineas = core.agregar_prefijo(texto, prefijo)
        if not lineas:
            raise HTTPException(400, "No hay líneas para enviar")

        corriendo = self.worker_task and not self.worker_task.done()
        if corriendo:
            # Anexa a la cola viva sin pisar el lote en curso.
            nuevas = [l for l in lineas if l not in self.cola]
            self.cola.extend(nuevas)
            await self.bus.emit({"tipo": "cola", "cola": self.cola})
            await self._emit_contadores()
            return {"agregadas": len(nuevas), "anexado": True}

        # Lote nuevo.
        self.destinos, self.destinos_ids, self.destinos_labels = (
            await self._resolver_destinos(destinos_raw)
        )
        self.intervalo = float(intervalo)
        self.prefijo_activo = prefijo
        # Reusa la sesión activa si su slug coincide (la creada/continuada por botón
        # gana); si no, crea una nueva (preserva el comportamiento cero-config).
        if not (self.sesion and self.sesion.slug == core.prefijo_slug(prefijo)):
            self.sesion = core.Sesion(prefijo)
        # Persiste el prefijo original (con punto) para poder continuarla luego.
        core.escribir_meta(self.sesion.dir, prefijo=prefijo)
        self.cola = list(lineas)
        self.stop = False
        self.pause_event.set()
        self._wake.clear()
        self.batch_inicio = time.monotonic()
        self.ultimo_envio = 0.0
        self.estado_envio = "enviando"

        await self.bus.emit({"tipo": "cola", "cola": self.cola})
        await self.bus.emit(
            {"tipo": "estado_envio", "estado": "enviando", "prefijo": prefijo}
        )
        await self._emit_sesion_activa()
        self.worker_task = asyncio.create_task(self._worker())
        return {"agregadas": len(lineas), "anexado": False}

    # --- controles -------------------------------------------------------- #
    async def pausar(self):
        self.pause_event.clear()
        self._wake.set()  # corta el sleep de intervalo/flood en curso
        self.estado_envio = "pausado"
        await self.bus.emit({"tipo": "estado_envio", "estado": "pausado"})

    async def reanudar(self):
        if self.estado_envio != "pausado":
            return
        self.pause_event.set()
        self.estado_envio = "enviando"
        await self.bus.emit({"tipo": "estado_envio", "estado": "enviando"})

    async def detener(self):
        self.stop = True
        self._wake.set()
        self.pause_event.set()  # libera el gate de pausa para que el worker salga
        self.cola = []
        await self.bus.emit({"tipo": "cola", "cola": self.cola})

    # --- esperas cancelables --------------------------------------------- #
    async def _sleep_cancelable(self, secs):
        if secs <= 0:
            return
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=secs)
        except asyncio.TimeoutError:
            pass
        finally:
            self._wake.clear()

    async def _esperar_intervalo(self):
        if not self.ultimo_envio or self.intervalo <= 0:
            return
        restante = (self.ultimo_envio + self.intervalo) - time.monotonic()
        if restante > 0:
            await self._sleep_cancelable(restante)

    # --- worker ----------------------------------------------------------- #
    async def _worker(self):
        try:
            while self.cola and not self.stop:
                await self.pause_event.wait()  # bloquea mientras esté en pausa
                if self.stop:
                    break

                await self._esperar_intervalo()
                if self.stop:
                    break
                await self.pause_event.wait()
                if self.stop:
                    break

                linea = self.cola[0]
                destino = self.destinos[self.enviados_total % len(self.destinos)]
                try:
                    await self.client.send_message(destino, linea)
                except FloodWaitError as e:
                    await self.bus.emit(
                        {"tipo": "flood", "segundos": e.seconds, "linea": linea}
                    )
                    await self._sleep_cancelable(e.seconds)
                    continue  # reintenta la misma línea
                except Exception as e:
                    await self.bus.emit({"tipo": "error", "mensaje": str(e)})
                    await self._sleep_cancelable(2)
                    continue

                self.ultimo_envio = time.monotonic()
                self.enviados_total += 1
                self.cola.pop(0)
                await self.bus.emit({"tipo": "linea_enviada", "linea": linea})
                await self._emit_progreso()
                await self._emit_contadores()
        finally:
            if self.stop:
                self.estado_envio = "detenido"
            else:
                self.estado_envio = "completado"
            await self.bus.emit(
                {
                    "tipo": "estado_envio",
                    "estado": self.estado_envio,
                    "pendientes": self.pendientes(),
                }
            )

    # --- emisión de progreso / contadores --------------------------------- #
    async def _emit_progreso(self):
        total = self.enviados_total + len(self.cola)
        eta = core.calcular_eta(self.enviados_total, total, self.batch_inicio)
        await self.bus.emit(
            {
                "tipo": "progreso",
                "actual": self.enviados_total,
                "total": total,
                "eta": eta,
            }
        )

    async def _emit_contadores(self):
        await self.bus.emit({"tipo": "contadores", **self.contadores()})

    async def _emit_sesion_activa(self):
        info = self.sesion.info() if self.sesion else {"id": None, "nombre": None, "prefijo": None}
        await self.bus.emit({"tipo": "sesion_activa", **info})

    # --- captura de respuestas del bot ------------------------------------ #
    async def _manejar_bot(self, texto, message_id, fuente=""):
        previo = self.estado_mensajes.get(message_id)
        if previo and previo["texto"] == texto:
            return  # edición sin cambios reales

        estado_previo = previo["estado"] if previo else None
        if "✅" in texto:
            estado_nuevo = "ok"
        elif "❌" in texto:
            estado_nuevo = "rechazada"
        else:
            estado_nuevo = estado_previo  # edición intermedia (⏳), conserva estado
        self.estado_mensajes[message_id] = {"texto": texto, "estado": estado_nuevo}

        if estado_nuevo == "ok":
            nuevos = self.sesion.guardar_respuesta(texto) if self.sesion else []
            if estado_previo != "ok":
                self.respuestas_recibidas += 1
                if estado_previo == "rechazada":
                    self.respuestas_rechazadas -= 1
                await self.bus.emit(
                    {
                        "tipo": "respuesta",
                        "estado": "ok",
                        "texto": texto,
                        "nuevos_cc": nuevos,
                        "fuente": fuente,
                    }
                )
            elif nuevos:
                await self.bus.emit(
                    {
                        "tipo": "respuesta",
                        "estado": "ok-edit",
                        "texto": texto,
                        "nuevos_cc": nuevos,
                        "fuente": fuente,
                    }
                )
        elif estado_nuevo == "rechazada" and estado_previo != "rechazada":
            self.respuestas_rechazadas += 1
            if estado_previo == "ok":
                self.respuestas_recibidas -= 1
            await self.bus.emit(
                {
                    "tipo": "respuesta",
                    "estado": "rechazada",
                    "texto": texto,
                    "nuevos_cc": [],
                    "fuente": fuente,
                }
            )
        await self._emit_contadores()


engine = Engine()


# --------------------------------------------------------------------------- #
# FastAPI
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start_client()
    yield
    await engine.stop_client()


app = FastAPI(lifespan=lifespan)


class EnviarReq(BaseModel):
    texto: str
    prefijo: str
    destinos: str  # coma-separados
    intervalo: float


class LoginCodeReq(BaseModel):
    phone: str


class LoginSignInReq(BaseModel):
    code: str
    password: str | None = None


class SesionNuevaReq(BaseModel):
    prefijo: str            # prefijo del usuario (ej: .zo)
    nombre: str | None = None


class SesionContinuarReq(BaseModel):
    prefijo: str            # slug / nombre de carpeta (de /api/prefijos)
    sesion: str             # timestamp / nombre de carpeta de la sesión


class SesionRenombrarReq(BaseModel):
    prefijo: str            # slug / nombre de carpeta
    sesion: str
    nombre: str


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
async def get_config():
    return {
        "autorizado": engine.autorizado,
        "phone": core.PHONE,
        "destinos": ",".join(core.DESTINOS_DEFAULT),
        "intervalo": core.INTERVALO_DEFAULT,
        "api_ok": bool(core.API_ID and core.API_HASH),
    }


@app.post("/api/enviar")
async def enviar(req: EnviarReq):
    destinos = [d for d in req.destinos.split(",") if d.strip()]
    return await engine.iniciar(req.texto, req.prefijo, destinos, req.intervalo)


@app.post("/api/pausar")
async def pausar():
    await engine.pausar()
    return {"ok": True}


@app.post("/api/reanudar")
async def reanudar():
    await engine.reanudar()
    return {"ok": True}


@app.post("/api/detener")
async def detener():
    await engine.detener()
    return {"ok": True}


@app.post("/api/login/send_code")
async def login_send_code(req: LoginCodeReq):
    await engine.login_send_code(req.phone)
    return {"ok": True}


@app.post("/api/login/sign_in")
async def login_sign_in(req: LoginSignInReq):
    await engine.login_sign_in(req.code, req.password)
    return {"ok": True, "autorizado": engine.autorizado}


# --- historial -------------------------------------------------------------- #
def _safe_dir(prefijo, sesion=None):
    """Resuelve respuestas/<prefijo>[/<sesion>] evitando path traversal."""
    base = core.RESPUESTAS_DIR.resolve()
    destino = (base / prefijo / sesion) if sesion else (base / prefijo)
    destino = destino.resolve()
    if base not in destino.parents and destino != base:
        raise HTTPException(400, "Ruta inválida")
    return destino


@app.get("/api/prefijos")
async def prefijos():
    if not core.RESPUESTAS_DIR.exists():
        return []
    nombres = sorted(
        p.name for p in core.RESPUESTAS_DIR.iterdir() if p.is_dir()
    )
    return nombres


@app.get("/api/sesiones/{prefijo}")
async def sesiones(prefijo):
    carpeta = _safe_dir(prefijo)
    if not carpeta.exists():
        return []
    dirs = sorted(
        (p for p in carpeta.iterdir() if p.is_dir() and p.name != "_ultima"),
        key=lambda p: p.name,
        reverse=True,
    )
    return [
        {"id": p.name, "nombre": core.leer_meta(p).get("nombre") or core.nombre_bonito(p.name)}
        for p in dirs
    ]


def _lote_vivo():
    """True si hay un envío en curso (enviando o pausado)."""
    corriendo = engine.worker_task and not engine.worker_task.done()
    return bool(corriendo) or engine.estado_envio in ("enviando", "pausado")


@app.post("/api/sesion/nueva")
async def sesion_nueva(req: SesionNuevaReq):
    if _lote_vivo():
        raise HTTPException(409, "No se puede cambiar de sesión durante un envío")
    sesion = core.Sesion(req.prefijo)
    nombre = (req.nombre or "").strip()
    campos = {"prefijo": req.prefijo}
    if nombre:
        campos["nombre"] = nombre[:200]
    core.escribir_meta(sesion.dir, **campos)
    engine.sesion = sesion
    engine.prefijo_activo = req.prefijo
    await engine._emit_sesion_activa()
    return sesion.info()


@app.post("/api/sesion/continuar")
async def sesion_continuar(req: SesionContinuarReq):
    if _lote_vivo():
        raise HTTPException(409, "No se puede cambiar de sesión durante un envío")
    carpeta = _safe_dir(req.prefijo, req.sesion)
    if not carpeta.exists():
        raise HTTPException(404, "La sesión no existe")
    sesion = core.Sesion(req.prefijo, sello=req.sesion, continuar=True)
    engine.sesion = sesion
    engine.prefijo_activo = req.prefijo
    await engine._emit_sesion_activa()
    return sesion.info()


@app.post("/api/sesion/renombrar")
async def sesion_renombrar(req: SesionRenombrarReq):
    nombre = req.nombre.strip()
    if not nombre:
        raise HTTPException(400, "El nombre no puede estar vacío")
    carpeta = _safe_dir(req.prefijo, req.sesion)
    if not carpeta.exists():
        raise HTTPException(404, "La sesión no existe")
    core.escribir_nombre(carpeta, nombre[:200])
    if engine.sesion and engine.sesion.dir.resolve() == carpeta:
        await engine._emit_sesion_activa()
    return {"id": req.sesion, "nombre": nombre[:200]}


@app.get("/api/respuesta/{prefijo}/{sesion}", response_class=PlainTextResponse)
async def respuesta(prefijo, sesion, tipo: str = "filtrada"):
    if tipo not in ("completa", "filtrada"):
        raise HTTPException(400, "tipo debe ser completa o filtrada")
    archivo = _safe_dir(prefijo, sesion) / f"{tipo}.txt"
    if not archivo.exists():
        return ""
    return archivo.read_text(encoding="utf-8")


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    engine.bus.register(websocket)
    await websocket.send_json(engine.snapshot())
    try:
        while True:
            await websocket.receive_text()  # mantener vivo / detectar cierre
    except WebSocketDisconnect:
        pass
    finally:
        engine.bus.unregister(websocket)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main():
    def _abrir():
        webbrowser.open(f"http://{HOST}:{PORT}")

    # Abre el navegador poco después de levantar el server.
    try:
        import threading

        threading.Timer(1.2, _abrir).start()
    except Exception:
        pass
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
