# Telegram Auto-Sender

Herramienta que monitorea tu portapapeles y reenvía mensajes a un chat de Telegram usando una cuenta de usuario (no un bot). Diseñada para enviar líneas a bots que procesan comandos tipo `.zo`, con sistema anti-spam adaptativo integrado.

---

## Requisitos previos

- **Python 3.9+** instalado ([descargar](https://www.python.org/downloads/))
- Una **cuenta de Telegram** con número de teléfono
- Credenciales de la API de Telegram (se explican abajo)

---

## Instalación paso a paso

### 1. Clonar el repositorio

```bash
git clone https://github.com/TU_USUARIO/telegram-auto-sender.git
cd telegram-auto-sender
```

### 2. Crear un entorno virtual (recomendado)

```bash
python3 -m venv .venv
source .venv/bin/activate    # macOS / Linux
# .venv\Scripts\activate     # Windows
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

Esto instala: `telethon`, `pyperclip`, `python-dotenv`, `fastapi`, `uvicorn`.

---

## Obtener las credenciales de Telegram

Necesitás un **API ID** y un **API Hash** de Telegram. Solo se obtienen una vez:

1. Entrá a [https://my.telegram.org/apps](https://my.telegram.org/apps)
2. Iniciá sesión con tu número de teléfono
3. Completá el formulario (nombre de app, descripción — puede ser lo que quieras)
4. Copiá el **api_id** y el **api_hash** que te da

> ⚠️ **Nunca compartas estas credenciales.** Son privadas como una contraseña.

---

## Configuración

### Crear el archivo `.env`

En la raíz del proyecto, creá un archivo llamado `.env` con este contenido:

```env
# Credenciales de Telegram (obligatorio)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_PHONE=+5491123456789

# Destino: nombre de usuario del bot o chat (sin @)
TELEGRAM_DESTINO=ZephyrChkV3Bot

# Prefijo que se agrega a cada línea (default: .zo)
TELEGRAM_PREFIJO=.zo

# Intervalo base entre mensajes en segundos (default: 8.0)
TELEGRAM_INTERVALO=8.0
```

### Variables opcionales

| Variable | Default | Descripción |
|---|---|---|
| `TELEGRAM_ANTISPAM_COOLDOWN` | `90.0` | Segundos de pausa cuando se detecta anti-spam |
| `TELEGRAM_RESPUESTA_ESPERA` | `1.5` | Segundos a esperar por respuesta después de cada envío |
| `TELEGRAM_ANTISPAM_KEYWORDS` | `antispam,anti spam,...` | Palabras clave separadas por coma que activan el cooldown |
| `TELEGRAM_ADAPTIVE_INCREMENTO` | `0.6` | Segundos que suma al intervalo tras cada anti-spam |
| `TELEGRAM_ADAPTIVE_EXTRA_MAX` | `3.0` | Máximo de segundos extra adaptativos |
| `TELEGRAM_ADAPTIVE_RECUPERACION` | `0.2` | Segundos que baja el intervalo tras envíos limpios |
| `TELEGRAM_ADAPTIVE_RECUPERACION_CADA` | `5` | Cada cuántos envíos limpios baja el ritmo |
| `TELEGRAM_LOG_FILE` | `telegram_antispam_log.csv` | Archivo de log (sin contenido sensible) |
| `TELEGRAM_LOG_MAX_SIZE_MB` | `10` | Tamaño máximo del log antes de rotar |
| `TELEGRAM_LOG_MAX_FILES` | `5` | Cantidad de archivos de log a conservar |
| `TELEGRAM_RESPUESTAS_FILE` | *(vacío)* | Si se setea, guarda respuestas ✅ en carpeta `respuestas/` |
| `TELEGRAM_TIEMPO_RESPUESTA` | `30` | Timeout para esperar respuestas |

---

## Uso

Hay dos formas de usarlo: la **interfaz web** (recomendada) o el **CLI por portapapeles** (legacy).

---

### Interfaz web (recomendada)

```bash
python app.py
```

Abre `http://127.0.0.1:8000` en el navegador. En vez de detectar el portapapeles,
**pegás el texto** en un cuadro y lo enviás desde la UI:

1. Escribí el **prefijo**, los **destinos** (coma-separados) y el **intervalo** — los
   valores de `.env` vienen precargados pero los podés cambiar sin tocar archivos.
2. Pegá el texto en el textarea (una línea = un mensaje) y apretá **Enviar**.
3. La **cola** baja línea por línea a medida que se envían, con barra de progreso, %, ETA
   y contadores (enviados, ✅ ok, ❌ rechazadas, ⏳ pendientes).
4. Controlás el lote con **Pausar / Reanudar / Detener** en vivo.
5. El panel **Respuestas en vivo** muestra cada respuesta del bot (✅/❌) con los datos `CC:`.
6. El **Historial** te deja navegar respuestas guardadas por prefijo → sesión, ver
   `completa.txt` / `filtrada.txt` y copiar o exportar la filtrada.

La config (`.env`) y la sesión (`anon.session`) son las mismas que usa el CLI: si ya te
autenticaste una vez, la web conecta directo. Si no, muestra un formulario de login
(teléfono → código → 2FA opcional).

---

### CLI por portapapeles (legacy)

```bash
python auto_sender.py .zo
```

El prefijo es un argumento **obligatorio**. La primera vez, Telethon te va a pedir:
1. Tu **número de teléfono** (si no está en `.env`)
2. Un **código de verificación** que te llega por Telegram
3. Si tenés **contraseña en dos pasos**, también te la va a pedir

> La sesión se guarda en `anon.session`. Mientras no lo borres, no vas a tener que autenticarte de nuevo.

### Cómo funciona

1. **Copiá texto** al portapapeles (Ctrl+C / Cmd+C)
2. El script detecta el texto nuevo automáticamente
3. Agrega el prefijo a cada línea (ej: `dato1` → `.zo dato1`)
4. Envía cada línea al destino con el intervalo configurado
5. Escucha respuestas del bot y las muestra en consola

### Modo simulación

Para probar sin enviar mensajes reales:

```bash
python auto_sender.py --dry-run
```

### Cambiar prefijo

```bash
python auto_sender.py --prefijo ".otro"
```

### Pausar y reanudar

Mientras el script corre, podés pausarlo creando un archivo `.pause` en la carpeta del proyecto:

```bash
touch .pause    # pausar
rm .pause       # reanudar
```

### Salir

Presioná `Ctrl+C`.

---

## Sistema anti-spam adaptativo

El bot tiene un sistema que se adapta automáticamente:

- **Si el bot destino responde con "antispam" o "flood"**: el script pausa automáticamente por el cooldown configurado (default 90s)
- **El intervalo entre envíos sube** gradualmente si se detectan varios anti-spams seguidos
- **El intervalo baja de a poco** cuando los envíos pasan sin problemas
- **Si Telegram impone un `FloodWaitError`**: el script espera exactamente lo que Telegram pide

---

## Respuestas del bot

Si configurás `TELEGRAM_RESPUESTAS_FILE`, las respuestas del bot que contengan ✅ se guardan automáticamente:

```
respuestas/
  zo/
    _ultima -> 2026-06-07_15-30-00   ← atajo a la sesión más reciente
    2026-06-07_15-30-00/
      completa.txt    ← respuestas completas con timestamp
      filtrada.txt    ← solo los datos después de "CC:", limpios
```

---

## Logs

El archivo `telegram_antispam_log.csv` registra cada intento de envío con:
- Timestamp, ID del lote, posición del mensaje
- Resultado (`ok`, `antispam`, `flood_wait`)
- Métricas de tiempo y estado adaptativo

El log rota automáticamente al alcanzar 10 MB (configurable).

---

## Múltiples destinos

Podés enviar a varios bots poniendo usuarios separados por coma:

```env
TELEGRAM_DESTINO=BotUno,BotDos,BotTres
```

Los mensajes se distribuyen en round-robin entre los destinos.

---

## Solución de problemas

| Problema | Solución |
|---|---|
| `Falta TELEGRAM_API_ID` | Completá las credenciales en `.env` |
| `No se encontro @usuario` | Abrí Telegram, buscá al usuario y empezá una conversación. Después volvé a ejecutar |
| `FloodWaitError` | El script lo maneja solo. Esperá |
| Se pide verificación cada vez | No borres `anon.session` |
| No se envían mensajes | Verificá que el destino sea correcto y que puedas enviarle mensajes manualmente desde Telegram |
| `pyperclip` no funciona en Linux | Instalá `xclip` o `xsel`: `sudo apt install xclip` |

---

## Estructura del proyecto

```
.
├── app.py                  # Interfaz web (FastAPI + WebSocket) — recomendada
├── static/index.html       # Frontend de la UI web
├── core.py                 # Lógica compartida (prefijo, sesiones, guardado)
├── auto_sender.py          # CLI legacy por portapapeles
├── requirements.txt        # Dependencias de Python
├── .env                    # Tu configuración (NO se sube a git)
├── .gitignore
├── anon.session            # Sesión de Telegram (NO se sube a git)
├── respuestas/             # Respuestas guardadas del bot
└── README.md
```
