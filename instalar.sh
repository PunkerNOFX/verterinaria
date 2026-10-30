#!/bin/bash
# Instala o actualiza la app de la veterinaria (PostgreSQL) en este server.
# Uso: sudo bash instalar.sh
# Se puede correr de nuevo para actualizar: no borra datos.
set -euo pipefail
cd "$(dirname "$0")"

PUERTO=8092          # el que se abre en el navegador
PUERTO_APP=8093      # interno, solo para nginx
APP=/opt/veterinaria-pg
WEB=/var/www/veterinaria-pg
DATOS=/var/lib/veterinaria-pg
RESPALDOS=/var/backups/veterinaria-pg
VIEJO=/var/lib/veterinaria/db.json   # datos del prototipo anterior

[ "$(id -u)" = 0 ] || { echo "Corré el instalador con sudo: sudo bash instalar.sh"; exit 1; }

primera_vez=1
[ -f /etc/nginx/sites-available/veterinaria-pg ] && primera_vez=0
if [ $primera_vez = 1 ]; then
  for p in $PUERTO $PUERTO_APP; do
    if ss -ltn | grep -q ":$p "; then
      echo "El puerto $p ya lo usa otro servicio. Avisale a Claude."; exit 1
    fi
  done
fi

echo "== Instalando programas necesarios"
apt-get update -qq
apt-get install -y -qq postgresql python3-venv nginx >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
  echo "Hace falta Python 3.11 o más nuevo (Debian 12 o posterior). Avisale a Claude."; exit 1; }

echo "== Copiando la app"
id veterinaria >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin veterinaria
mkdir -p "$APP" "$WEB" "$DATOS/archivos" "$RESPALDOS"
rm -rf "$APP/app"
cp -r app requirements.txt "$APP/"
cp web/* "$WEB/"
chown -R veterinaria:veterinaria "$DATOS"
chmod 750 "$DATOS"
chmod 700 "$RESPALDOS"

[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

cat > /usr/local/bin/veterinaria-admin <<EOF
#!/bin/bash
# Administración de la app de la veterinaria. Ejemplo: sudo veterinaria-admin usuarios
[ "\$(id -u)" = 0 ] || { echo "Usalo con sudo: sudo veterinaria-admin \$*"; exit 1; }
cd $APP && exec runuser -u postgres -- $APP/venv/bin/python -m app.admin "\$@"
EOF
chmod 755 /usr/local/bin/veterinaria-admin

echo "== Base de datos"
systemctl enable --now postgresql >/dev/null 2>&1 || true
runuser -u postgres -- psql -qtAc "SELECT 1 FROM pg_roles WHERE rolname = 'veterinaria'" | grep -q 1 \
  || runuser -u postgres -- psql -qc "CREATE ROLE veterinaria LOGIN"
runuser -u postgres -- psql -qtAc "SELECT 1 FROM pg_database WHERE datname = 'veterinaria'" | grep -q 1 \
  || runuser -u postgres -- createdb veterinaria
veterinaria-admin migrar

echo "== Servicio"
cat > /etc/systemd/system/veterinaria-pg.service <<EOF
[Unit]
Description=App de gestión veterinaria
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=veterinaria
WorkingDirectory=$APP
Environment=VET_ARCHIVOS=$DATOS/archivos
ExecStart=$APP/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $PUERTO_APP
Restart=always
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable veterinaria-pg >/dev/null 2>&1
systemctl restart veterinaria-pg

cat > /etc/nginx/sites-available/veterinaria-pg <<EOF
server {
    listen $PUERTO;
    root $WEB;
    index index.html;
    client_max_body_size 30m;
    location / {
        add_header Cache-Control no-cache;
        add_header X-Content-Type-Options nosniff;
        add_header X-Frame-Options DENY;
        add_header Referrer-Policy same-origin;
    }
    location /api/ {
        proxy_pass http://127.0.0.1:$PUERTO_APP;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF
ln -sf /etc/nginx/sites-available/veterinaria-pg /etc/nginx/sites-enabled/veterinaria-pg
nginx -t -q
systemctl reload nginx

echo "== Respaldo diario"
cat > /etc/cron.daily/veterinaria-pg <<EOF
#!/bin/sh
# Copia de la base todas las noches; guarda las últimas 14.
cd /tmp && runuser -u postgres -- pg_dump -Fc veterinaria > $RESPALDOS/veterinaria-\$(date +%F).dump
tar -czf $RESPALDOS/archivos-\$(date +%F).tar.gz -C $DATOS archivos
find $RESPALDOS -type f -mtime +14 -delete
EOF
chmod 755 /etc/cron.daily/veterinaria-pg

for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -fs "http://127.0.0.1:$PUERTO_APP/api/salud" >/dev/null && break
  sleep 1
done
curl -fs "http://127.0.0.1:$PUERTO_APP/api/salud" >/dev/null || {
  echo "La app no arrancó. Mandale a Claude lo que dice: journalctl -u veterinaria-pg -n 50"; exit 1; }

if ! runuser -u postgres -- psql -qtAc "SELECT 1 FROM veterinarias LIMIT 1" veterinaria | grep -q 1; then
  veterinaria-admin configurar
  if [ -f "$VIEJO" ]; then
    echo
    read -rp "¿Pasar los datos que ya cargaron en el prototipo ($VIEJO)? [s/n] " r </dev/tty
    if [ "$r" = s ] || [ "$r" = S ]; then
      cp "$VIEJO" "$RESPALDOS/db-prototipo-$(date +%F).json"
      VID=$(runuser -u postgres -- psql -qtAc "SELECT min(id) FROM veterinarias" veterinaria)
      veterinaria-admin importar - --veterinaria "$VID" < "$VIEJO"
    fi
  fi
fi

echo
echo "Listo. Entrá a http://$(hostname -I | awk '{print $1}'):$PUERTO"
echo "Para crear más usuarios: sudo veterinaria-admin --help"
