# Runbook: backups diarios y restauración

**Qué hay instalado** (Story 4.4, instalación en `deploy/README.md` paso 12):

- `cc-backup.timer` dispara `cc-backup.service` todos los días a las
  **04:30 UTC** (con `Persistent=true`: si el VPS estaba apagado, corre al
  siguiente arranque).
- `deploy/backup_db.sh` hace `pg_dump --format=custom` de la base `cc`
  **dentro del contenedor** `lohari-postgres` (socket local, sin password en
  el script), escribe a `/var/backups/cc/cc-<UTC>.dump` (root, modo 600),
  **verifica** el dump con `pg_restore --list` y poda los de más de 14 días.

## Verificación rápida (semanal, 1 minuto)

```bash
systemctl list-timers cc-backup.timer          # próximo disparo programado
systemctl status cc-backup.service             # última corrida: "backup done"
sudo ls -lh /var/backups/cc | tail -5          # dumps recientes, tamaño sano
```

Señales de alarma: el timer no figura, la última corrida falló, no hay dump
de hoy/ayer, o el tamaño cayó bruscamente respecto a los anteriores.

Corrida manual (no espera al timer):

```bash
sudo systemctl start cc-backup.service && systemctl status cc-backup.service
```

## Simulacro de restauración (mensual — un backup no probado no es backup)

Restaura el último dump a una base scratch y comprueba datos reales. No toca
la base `cc` en uso.

```bash
last=$(sudo ls -t /var/backups/cc/cc-*.dump | head -1) && echo "$last"

sudo docker exec lohari-postgres psql -U postgres -c 'CREATE DATABASE cc_restore;'
# El dump es root-only: `sudo cat` lo lee (una redirección `< "$last"` la
# abriría TU shell sin privilegios y fallaría).
sudo cat "$last" | sudo docker exec -i lohari-postgres pg_restore \
    -U postgres -d cc_restore --no-owner

# Sanity: las tablas y filas esperadas existen.
sudo docker exec lohari-postgres psql -U postgres -d cc_restore \
    -c 'SELECT count(*) AS users FROM users;' \
    -c 'SELECT count(*) AS tenants FROM tenants;' \
    -c 'SELECT version_num FROM alembic_version;'

sudo docker exec lohari-postgres psql -U postgres -c 'DROP DATABASE cc_restore;'
```

Pasa si: `pg_restore` termina sin errores, los conteos son plausibles y
`alembic_version` coincide con la migración desplegada
(`cd /srv/cc/backend && sudo -u cc .venv/bin/alembic current`).

## Restauración ante desastre (la base `cc` se perdió o corrompió)

```bash
# 1. Frenar el servicio (nada debe escribir durante la restauración):
sudo systemctl stop cc-core

# 2. Elegir el dump (normalmente el más reciente):
last=$(sudo ls -t /var/backups/cc/cc-*.dump | head -1) && echo "$last"

# 3. Recrear la base y restaurar (WITH (FORCE) corta conexiones colgadas):
sudo docker exec lohari-postgres psql -U postgres \
    -c 'DROP DATABASE IF EXISTS cc WITH (FORCE);'
sudo docker exec lohari-postgres psql -U postgres -c 'CREATE DATABASE cc OWNER cc;'
sudo cat "$last" | sudo docker exec -i lohari-postgres pg_restore \
    -U postgres -d cc --no-owner --role=cc

# 4. Verificar y rearrancar:
sudo docker exec lohari-postgres psql -U postgres -d cc \
    -c 'SELECT count(*) FROM users;' -c 'SELECT version_num FROM alembic_version;'
sudo systemctl start cc-core
curl -s -o /dev/null -w '%{http_code}\n' https://cc.lohari.com.mx/api/health  # 200
```

Se pierde lo escrito entre el último backup y el incidente (RPO ≤ 24 h).
El dump incluye `alembic_version`, así que el esquema queda consistente; si
el código desplegado es más nuevo que el dump, corré además
`sudo -u cc .venv/bin/alembic upgrade head` desde `/srv/cc/backend`.

## Notas operativas

- **El backup vive en el MISMO VPS.** Cubre borrados/corrupción de la base,
  no la pérdida del VPS entero. Recomendado: copia off-site periódica, p. ej.
  `rsync -a --include 'cc-*.dump' --exclude '*' root@37.27.12.92:/var/backups/cc/ ./cc-backups/`
  desde una máquina del owner.
- **Si recrean el contenedor de Postgres** el nombre/IP pueden cambiar: el
  script usa el **nombre** (`lohari-postgres`), que sobrevive a un cambio de
  IP; si el nombre cambia, ajustá `Environment=PG_CONTAINER=…` en
  `cc-backup.service` (y `DATABASE_URL` del backend, ver `deploy/README.md`
  paso 5).
- Los dumps contienen datos de **todos** los tenants: tratálos como
  credenciales (root-only, nunca salen del control del owner).
