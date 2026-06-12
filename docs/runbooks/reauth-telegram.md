# Runbook: re-autenticación de Telegram (`AuthKeyError`)

**Cuándo:** la sesión de Telethon en el VPS murió. Síntomas:

- `AuthKeyError` / `AuthKeyUnregisteredError` en los logs de `cc-core`, o
- el watchdog (Story 4.1, cuando esté desplegado) pausó los envíos globales
  y alertó por pérdida de sesión, o
- los envíos "funcionan" pero Telegram rechaza cada llamada.

Causas típicas: Telegram revocó la sesión (login sospechoso desde IP de
datacenter, "Terminar todas las sesiones" desde el teléfono), o **dos
procesos MTProto usaron el mismo `anon.session`** (regla single-owner rota —
nunca corras una segunda instancia de `cc-core` ni un script Telethon contra
el mismo archivo de sesión).

El flujo completo es: **detectar → pausa global → re-autenticar EN el VPS →
reanudación explícita.** Nunca se reanuda solo.

## 1. Detectar y confirmar

```bash
sudo journalctl -u cc-core --since "-2h" | grep -iE "authkey|unauthorized"
```

Si hay `AuthKeyError`/`AuthKeyUnregisteredError`, la sesión está muerta:
re-autenticar es la única salida. Un reinicio del servicio NO la arregla.

## 2. Pausa global de envíos

- **Con Story 4.1 desplegado:** el watchdog ya pausó los envíos en cuanto
  detectó la desautorización. Confirmalo en la UI / logs antes de seguir.
- **Sin watchdog (fallback duro):** pará el proceso para que nada siga
  intentando enviar con una sesión muerta:

```bash
sudo systemctl stop cc-core
```

## 3. Re-autenticar — SIEMPRE en el VPS

La sesión se crea **en el VPS, nunca se copia desde otra máquina**: una
sesión creada en otro lado corre riesgo de invalidarse al usarse por primera
vez desde la IP del datacenter (deep-dive de riesgos de la arquitectura).

```bash
# 3a. Apartá la sesión muerta (el script re-autentica en el mismo archivo,
#     pero un archivo corrupto produce errores crudos de sqlite/Telethon —
#     ante cualquier duda, movelo y arrancá limpio):
sudo mv /var/lib/cc/anon.session /var/lib/cc/anon.session.dead-$(date +%F)

# 3b. Re-auth interactiva (teléfono → código → 2FA opcional).
#     Requiere TELEGRAM_API_ID / TELEGRAM_API_HASH en /srv/cc/backend/.env:
cd /srv/cc/backend
sudo -u cc .venv/bin/python -m scripts.telegram_auth

# 3c. Verificá permisos y dueño (el script los fuerza, confirmalo igual):
ls -l /var/lib/cc/anon.session    # debe ser: -rw------- cc cc
```

Si el script dice `already authorized` pero el paso 1 mostró `AuthKeyError`,
estás re-usando el archivo muerto: repetí 3a (movelo) y volvé a correr 3b.

## 4. Reanudación explícita — decisión del owner

```bash
sudo systemctl start cc-core
sudo journalctl -u cc-core -f      # arranque limpio, sin AuthKeyError
```

- **Con Story 4.1:** los envíos siguen en pausa global hasta que el owner
  los reanude explícitamente desde la UI. La reanudación **nunca es
  automática** — antes de reanudar, entendé POR QUÉ murió la sesión (si fue
  la regla single-owner, arreglá eso primero o la nueva sesión muere igual).
- **Sin watchdog:** arrancar `cc-core` rearma el servicio; verificá con un
  lote corto de prueba antes de avisar a los clientes.

## 5. Después del incidente

- Borrá la sesión muerta cuando todo esté verde:
  `sudo rm /var/lib/cc/anon.session.dead-*`
- Registrá fecha y causa (los disparos del watchdog quedan en los logs
  estructurados — Story 4.1/4.3).
- Si Telegram revocó por comportamiento, repasá el ritmo de envío antes de
  reanudar a pleno (ver `plan-de-lanzamiento.md`, regla de retroceso).
