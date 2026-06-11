# Telegram Auto-Sender

Herramienta que envía líneas de texto a uno o más chats de Telegram usando una **cuenta de usuario** (no un bot), agregando un prefijo a cada línea (ej: `dato1` → `.zo dato1`). Las respuestas del bot que contienen ✅ se guardan automáticamente en disco, con los datos `CC:` extraídos a un archivo filtrado.

Tiene dos interfaces que comparten la misma lógica y la misma sesión de Telegram:

- **Interfaz web** (`app.py`) — la recomendada: pegás el texto, ves la cola bajar línea por línea, pausás/reanudás/detenés en vivo, y navegás el historial de respuestas.
- **CLI por portapapeles** (`auto_sender.py`) — legacy: monitorea el portapapeles y envía lo que copies.

---

## Requisitos previos

- **Python 3.10+** instalado ([descargar](https://www.python.org/downloads/))
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

En la raíz del proyecto, creá un archivo llamado `.env` con este contenido:

```env
# Credenciales de Telegram (obligatorio)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_PHONE=+5491123456789

# Destino(s): nombre de usuario del bot o chat (sin @), separados por coma
TELEGRAM_DESTINO=MiBotDestino

# Intervalo entre mensajes en segundos (default: 8.0)
TELEGRAM_INTERVALO=8.0
```

Esas son **todas** las variables. En la interfaz web, destino e intervalo son solo
valores precargados: los podés cambiar desde la UI sin tocar archivos. El prefijo
no tiene variable de entorno — se escribe en la UI o se pasa como argumento al CLI.

---

## Uso

### Interfaz web (recomendada)

```bash
python app.py
```

Abre solo el navegador en `http://127.0.0.1:8000`. Desde ahí:

1. Escribí el **prefijo**, los **destinos** (coma-separados) y el **intervalo** — los
   valores de `.env` vienen precargados. El campo prefijo sugiere prefijos ya usados
   (ojo: sugiere el nombre de carpeta, sin el punto inicial — ej: `zo`, no `.zo`).
2. Pegá el texto en el textarea (una línea = un mensaje) — el botón **Pegar** lo trae
   del portapapeles — y apretá **Enviar**. Si hay un envío en curso, las líneas nuevas
   se **anexan** a la cola (sin duplicar las que siguen pendientes), manteniendo los
   destinos, el intervalo y la sesión del lote en curso.
3. La **cola** baja línea por línea, con barra de progreso, %, ETA y contadores
   (enviados, ✅ ok, ❌ rechazadas, ⏳ pendientes, en cola). Los contadores acumulan
   mientras el servidor corre — no se reinician entre lotes.
4. Controlás el lote con **Pausar / Reanudar / Detener** en vivo (detener vacía la cola).
5. El panel **Respuestas en vivo** tiene dos columnas: **Completa** (cada respuesta
   del bot con su ✅/❌; muestra las últimas 40) y **Filtrada** (solo los datos `CC:`
   nuevos; muestra los últimos 200). El archivo en disco siempre tiene todo.
6. Las respuestas se guardan en **sesiones de guardado** con nombre: el botón
   **Nueva** crea una (con nombre opcional), **Renombrar** la renombra, y si no creás
   ninguna se crea una automática al enviar.
7. El **Historial** navega las respuestas guardadas por prefijo → sesión, mostrando
   `completa.txt` y `filtrada.txt` lado a lado, cada una con **Copiar** y **Exportar**.
   Por defecto **sigue en vivo** la sesión activa (se refresca solo con cada respuesta);
   si navegás a otra sesión, el botón **↻ Ver sesión actual** te trae de vuelta.
   **Continuar esta sesión** reanuda una sesión vieja sin duplicar datos `CC:` ya guardados.

La config (`.env`) y la sesión de Telegram (`anon.session`) son las mismas que usa el
CLI: si ya te autenticaste una vez, la web conecta directo. Si no, muestra un formulario
de login (teléfono → código → 2FA opcional).

> Nota: no se puede crear ni continuar una sesión de guardado mientras hay un envío
> en curso o pausado — el servidor lo rechaza con un error. Esperá a que termine el
> lote o presioná **Detener**.

### CLI por portapapeles (legacy)

```bash
python auto_sender.py .zo
```

El prefijo es un argumento posicional **obligatorio** (único argumento; no hay flags).
Destino, teléfono e intervalo salen de `.env` (si falta alguno, el script avisa y sale).
La primera vez, Telethon te va a pedir:

1. Un **código de verificación** que te llega por Telegram
2. Si tenés **contraseña en dos pasos**, también te la va a pedir

> La sesión se guarda en `anon.session`. Mientras no lo borres, no vas a tener que autenticarte de nuevo.

Cómo funciona:

1. **Copiá texto** al portapapeles (Ctrl+C / Cmd+C)
2. El script detecta el texto nuevo automáticamente (copiar lo mismo dos veces no re-envía)
3. Agrega el prefijo a cada línea y descarta líneas duplicadas del lote
4. Envía cada línea al destino con el intervalo configurado, mostrando progreso y ETA
5. Escucha respuestas del bot, las muestra en consola y las guarda en `respuestas/`

Para salir: `Ctrl+C`. (El CLI no tiene pausa — pausar/reanudar existe solo en la web.)

---

## Manejo de límites de Telegram

El intervalo entre envíos es **constante** (`TELEGRAM_INTERVALO`, editable en la web).
Si Telegram impone un `FloodWaitError`, el programa espera los segundos que Telegram
pide y reintenta la **misma** línea — no se pierde ninguna. Cualquier otro error de
envío también reintenta la misma línea (cada 2 s en la web): si una línea falla
siempre, bloquea la cola hasta que presiones **Detener**.

---

## Respuestas guardadas

Las respuestas del bot que contienen ✅ se guardan **siempre** (no hay que activar nada),
organizadas por prefijo y sesión:

```
respuestas/
  zo/                              ← slug del prefijo (.zo → zo)
    _ultima -> 2026-06-09_21-30-00   ← atajo a la sesión más reciente
    2026-06-09_21-30-00/
      completa.txt    ← todas las respuestas, con timestamp
      filtrada.txt    ← solo los datos después de "CC:", sin duplicados
      meta.json       ← nombre de la sesión y prefijo original (solo sesiones de la web)
```

- Los datos `CC:` se deduplican **por sesión**: el mismo dato no se escribe dos veces
  en `filtrada.txt` (cada dato se corta en la palabra `Status` si aparece).
- Las **ediciones** de mensajes del bot también se capturan: una respuesta que pasa
  de ⏳ a ✅ se guarda cuando llega la edición, y una edición ❌→✅ corrige los contadores.
- **Continuar** una sesión vieja (desde el Historial de la web) precarga lo ya guardado,
  así los re-envíos no duplican datos.
- El nombre visible de cada sesión vive en `meta.json` (editable con **Renombrar**);
  el nombre de la carpeta (timestamp) es el ID estable.

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
| `Falta TELEGRAM_API_ID` / `Falta TELEGRAM_PHONE` / `Falta TELEGRAM_DESTINO` | Completá esas variables en `.env` |
| `No se encontro @usuario` | Abrí Telegram, buscá al usuario y empezá una conversación. Después volvé a ejecutar |
| `FloodWaitError` | El programa lo maneja solo. Esperá |
| Se pide verificación cada vez | No borres `anon.session` |
| No se envían mensajes | Verificá que el destino sea correcto y que puedas enviarle mensajes manualmente desde Telegram |
| La web muestra el formulario de login | Falta autorizar la cuenta: teléfono → código → 2FA, una sola vez |
| `pyperclip` no funciona en Linux | Instalá `xclip` o `xsel`: `sudo apt install xclip` |

---

## Estructura del proyecto

```
.
├── app.py                  # Interfaz web (FastAPI + WebSocket) — recomendada
├── static/index.html       # Frontend de la UI web (sin build, vanilla JS)
├── core.py                 # Lógica compartida (prefijo, sesiones, guardado, CC:)
├── auto_sender.py          # CLI legacy por portapapeles
├── requirements.txt        # Dependencias de Python
├── .env                    # Tu configuración (NO se sube a git)
├── .gitignore
├── anon.session            # Sesión de Telegram (NO se sube a git)
├── respuestas/             # Respuestas guardadas, por prefijo/sesión
└── README.md
```
