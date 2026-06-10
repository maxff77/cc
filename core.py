"""Lógica compartida entre el CLI (auto_sender.py) y la UI web (app.py).

Sin I/O de terminal: solo config, transformación de texto y persistencia de
respuestas. Tanto el sender por portapapeles como la app FastAPI importan de acá
para no duplicar la lógica.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent

# --- Config (valores por defecto desde .env; la UI los puede sobreescribir) ---
load_dotenv(ROOT / ".env")

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE = os.getenv("TELEGRAM_PHONE", "")
DESTINOS_DEFAULT = [
    d.strip().lstrip("@")
    for d in os.getenv("TELEGRAM_DESTINO", "").split(",")
    if d.strip()
]
INTERVALO_DEFAULT = float(os.getenv("TELEGRAM_INTERVALO", "8.0"))

RESPUESTAS_DIR = ROOT / "respuestas"

# Captura el dato que sigue a "CC:" (case-insensitive) hasta el fin de línea.
RE_CC = re.compile(r"(?i)\bCC\s*:\s*([^\n]+)")


def prefijo_slug(prefijo):
    """Slug de carpeta para un prefijo (ej: '.zo' -> 'zo')."""
    return prefijo.lstrip(".").replace(" ", "_") or "sin_prefijo"


def agregar_prefijo(texto, prefijo):
    """Agrega el prefijo a cada línea si no lo tiene ya. Deduplica líneas."""
    lineas = texto.strip().split("\n")
    resultado = []
    vistos = set()
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        mensaje = linea if linea.startswith(prefijo + " ") else f"{prefijo} {linea}"
        if mensaje not in vistos:
            resultado.append(mensaje)
            vistos.add(mensaje)
    return resultado


def extraer_cc(texto):
    """Devuelve los datos que siguen a 'CC:' en el texto (sin el 'Status...')."""
    datos = []
    for m in RE_CC.findall(texto):
        dato = m.split("Status")[0].strip()
        if dato:
            datos.append(dato)
    return datos


# --- metadatos de sesión (etiqueta amigable en meta.json) ------------------- #
RE_SELLO = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})$")


def nombre_bonito(sello):
    """'2026-06-09_21-30-00' -> '2026-06-09 21:30:00'. Si no matchea, lo deja igual."""
    m = RE_SELLO.match(sello or "")
    if not m:
        return sello
    return f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}"


def leer_meta(dir):
    """Lee meta.json de una sesión. Devuelve {} si falta o está corrupto."""
    archivo = Path(dir) / "meta.json"
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def escribir_meta(dir, **campos):
    """Crea/actualiza meta.json fusionando campos (escritura atómica). Conserva 'creada'."""
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    meta = leer_meta(dir)
    meta.update(campos)
    meta.setdefault("creada", time.strftime("%Y-%m-%dT%H:%M:%S"))
    tmp = dir / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, dir / "meta.json")


def escribir_nombre(dir, nombre):
    """Atajo: setea solo el 'nombre' en meta.json."""
    escribir_meta(dir, nombre=nombre)


class Sesion:
    """Una sesión de guardado para un prefijo dado.

    Encapsula la carpeta de respuestas, el dedup de líneas CC: y el symlink
    `_ultima`. Antes esto vivía en globals de módulo; ahora es estado de
    instancia para soportar varios prefijos sin reiniciar el proceso.
    """

    def __init__(self, prefijo, base_dir=RESPUESTAS_DIR, sello=None, continuar=False):
        self.prefijo = prefijo
        self.slug = prefijo_slug(prefijo)
        sello = sello or time.strftime("%Y-%m-%d_%H-%M-%S")
        self.dir = Path(base_dir) / self.slug / sello
        self._cc_guardadas = set()
        self._symlink_actualizado = False
        if continuar:
            self.cargar_cc_existentes()

    def _actualizar_symlink_ultima(self):
        """Apunta respuestas/<prefijo>/_ultima a la carpeta de esta sesión."""
        if self._symlink_actualizado:
            return
        enlace = self.dir.parent / "_ultima"
        if enlace.is_symlink() or enlace.exists():
            enlace.unlink()
        # Relativo (solo el nombre) para que sobreviva si se mueve respuestas/.
        enlace.symlink_to(self.dir.name)
        self._symlink_actualizado = True

    def guardar_respuesta(self, texto):
        """Guarda la respuesta completa y anexa a filtrada.txt los CC: nuevos.

        Devuelve la lista de datos CC: nuevos guardados en esta llamada.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        self._actualizar_symlink_ultima()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")

        with (self.dir / "completa.txt").open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {texto}\n\n")

        nuevos = []
        for dato in extraer_cc(texto):
            if dato not in self._cc_guardadas:
                self._cc_guardadas.add(dato)
                nuevos.append(dato)
        if nuevos:
            with (self.dir / "filtrada.txt").open("a", encoding="utf-8") as f:
                f.write("\n".join(nuevos) + "\n")
        return nuevos

    def cargar_cc_existentes(self):
        """Precarga _cc_guardadas desde filtrada.txt (una línea = un dato CC).

        Permite continuar una sesión vieja sin re-anexar CC: duplicados. Devuelve
        la cantidad de datos cargados.
        """
        archivo = self.dir / "filtrada.txt"
        if not archivo.exists():
            return 0
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea:
                self._cc_guardadas.add(linea)
        return len(self._cc_guardadas)

    def info(self):
        """Datos de la sesión para el cliente: id (carpeta), nombre y prefijo.

        El prefijo original (con punto, ej. '.zo') se guarda en meta.json al crear
        la sesión; si no está (sesiones legacy) cae al slug.
        """
        meta = leer_meta(self.dir)
        nombre = meta.get("nombre") or nombre_bonito(self.dir.name)
        return {"id": self.dir.name, "nombre": nombre, "prefijo": meta.get("prefijo") or self.slug, "slug": self.slug}


def formatear_progreso(actual, total, inicio):
    """Línea de progreso con barra, porcentaje y ETA (para el CLI)."""
    pct = actual / total
    elapsed = time.monotonic() - inicio
    if actual > 1:
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
    return f"\r    [{bar}] {int(pct*100):3d}% ({actual}/{total}) | ETA: {eta_str}"


def calcular_eta(actual, total, inicio):
    """ETA en segundos para la UI. Devuelve None si aún no se puede estimar."""
    if actual <= 1 or actual > total:
        return None
    elapsed = time.monotonic() - inicio
    return (elapsed / actual) * (total - actual)


async def esperar_intervalo(ultimo_envio, intervalo):
    """Espera lo que falte para respetar el intervalo entre envíos (CLI)."""
    if not ultimo_envio or intervalo <= 0:
        return
    restante = (ultimo_envio + intervalo) - time.monotonic()
    if restante > 0:
        await asyncio.sleep(restante)
