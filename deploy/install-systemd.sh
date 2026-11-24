#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(id -un)"
APP_GROUP="$(id -gn)"

if [[ "$PROJECT_DIR" == *" "* ]]; then
    echo "The project path must not contain spaces: $PROJECT_DIR" >&2
    exit 1
fi

install_unit() {
    local template="$1" target="$2"
    sed \
        -e "s|__APP_USER__|$APP_USER|g" \
        -e "s|__APP_GROUP__|$APP_GROUP|g" \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        "$PROJECT_DIR/deploy/$template" |
        sudo tee "/etc/systemd/system/$target" >/dev/null
}

echo "Installing native packages..."
sudo apt-get update
# curl probes /healthz/, smartmontools reports failing disks (the watchdog
# checks `smartctl -H`). smartd is enabled further down.
sudo apt-get install -y python3-venv curl smartmontools

echo "Creating Python environment..."
python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    SECRET_KEY="$("$PROJECT_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(50))')"
    sed -i "s|replace-with-a-long-random-secret|$SECRET_KEY|" "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env"
    echo "Created .env. Review its domain and email settings after installation."
fi

if grep -q '^GUNICORN_BIND=' "$PROJECT_DIR/.env"; then
    sed -i 's/^GUNICORN_BIND=.*/GUNICORN_BIND=0.0.0.0:80/' "$PROJECT_DIR/.env"
else
    printf '\nGUNICORN_BIND=0.0.0.0:80\n' >>"$PROJECT_DIR/.env"
fi

# Where backups go. Keep this and BACKUP_DIR in /etc/portfolio-monitor.env in
# sync; a path outside /var/backups also needs adding to the backup unit's
# ReadWritePaths.
BACKUP_DIR="$(sed -n 's/^BACKUP_DIR=//p' "$PROJECT_DIR/.env" | tail -1)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/portfolio}"

echo "Creating persistent data directories..."
sudo install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 /var/lib/portfolio
sudo install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 /var/lib/portfolio/staticfiles
sudo install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 /var/lib/portfolio/media
# Archives contain .env, so the directory is private to the app account.
sudo install -d -o "$APP_USER" -g "$APP_GROUP" -m 0700 "$BACKUP_DIR"

echo "Installing systemd service..."
install_unit portfolio.service.template portfolio.service

echo "Installing daily statistics cleanup timer..."
install_unit portfolio-purge.service.template portfolio-purge.service
sudo cp "$PROJECT_DIR/deploy/portfolio-purge.timer.template" \
    /etc/systemd/system/portfolio-purge.timer

echo "Installing daily GitHub project sync timer..."
install_unit portfolio-github-sync.service.template portfolio-github-sync.service
sudo cp "$PROJECT_DIR/deploy/portfolio-github-sync.timer.template" \
    /etc/systemd/system/portfolio-github-sync.timer

echo "Installing monthly media cleanup timer..."
install_unit portfolio-media-prune.service.template portfolio-media-prune.service
sudo cp "$PROJECT_DIR/deploy/portfolio-media-prune.timer.template" \
    /etc/systemd/system/portfolio-media-prune.timer

echo "Installing daily backup timer..."
install_unit portfolio-backup.service.template portfolio-backup.service
sudo cp "$PROJECT_DIR/deploy/portfolio-backup.timer.template" \
    /etc/systemd/system/portfolio-backup.timer

echo "Installing alert and health-check helpers..."
sudo install -m 0755 "$PROJECT_DIR/deploy/portfolio-notify.sh" /usr/local/bin/portfolio-notify
sudo install -m 0755 "$PROJECT_DIR/deploy/portfolio-watchdog.sh" /usr/local/bin/portfolio-watchdog
install_unit portfolio-alert.service.template portfolio-alert.service
install_unit portfolio-watchdog.service.template portfolio-watchdog.service
sudo cp "$PROJECT_DIR/deploy/portfolio-watchdog.timer.template" \
    /etc/systemd/system/portfolio-watchdog.timer

MONITOR_ENV=/etc/portfolio-monitor.env
if [[ ! -f "$MONITOR_ENV" ]]; then
    echo "Creating alert configuration with a new ntfy topic..."
    NTFY_TOPIC="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    SITE_HOST="$(
        sed -n 's/^SITE_URL=//p' "$PROJECT_DIR/.env" | tail -1 |
            sed -E 's#^https?://##; s#/.*$##'
    )"
    SITE_HOST="${SITE_HOST:-localhost}"
    TEMP_ENV="$(mktemp)"
    sed \
        -e "s|^NTFY_URL=$|NTFY_URL=https://ntfy.sh/$NTFY_TOPIC|" \
        -e "s|^HEALTH_HOST=.*|HEALTH_HOST=$SITE_HOST|" \
        -e "s|^BACKUP_DIR=.*|BACKUP_DIR=$BACKUP_DIR|" \
        "$PROJECT_DIR/deploy/portfolio-monitor.env.example" >"$TEMP_ENV"
    sudo install -m 0600 -o root -g root "$TEMP_ENV" "$MONITOR_ENV"
    rm -f "$TEMP_ENV"
fi

sudo systemctl daemon-reload
# Restart rather than `enable --now`: on an update the running process still has
# the old code, and the watchdog must not start checking /healthz/ before the
# new endpoint is actually serving. `restart` also starts a stopped service.
sudo systemctl enable portfolio.service
sudo systemctl restart portfolio.service
sudo systemctl enable --now portfolio-purge.timer
sudo systemctl enable --now portfolio-github-sync.timer
sudo systemctl enable --now portfolio-media-prune.timer

# Run once now so the watchdog has a baseline instead of reporting "no backup"
# until 02:30 tomorrow. A failure is reported by portfolio-alert.
sudo systemctl enable --now portfolio-backup.timer
if ! sudo systemctl start portfolio-backup.service; then
    echo "Warning: the first backup failed; inspect journalctl -u portfolio-backup.service" >&2
fi

sudo systemctl enable --now portfolio-watchdog.timer

# On Ubuntu, smartd.service is an alias linked to smartmontools.service, which
# `systemctl enable` refuses to touch ("Refusing to operate on linked unit
# file") even though it is already enabled. Check first, then only start it.
if ! systemctl is-enabled --quiet smartd 2>/dev/null; then
    sudo systemctl enable smartd || true
fi
if ! sudo systemctl start smartd; then
    echo "Warning: smartd could not be started; the watchdog still checks" >&2
    echo "disk health with smartctl, but continuous monitoring is off." >&2
fi

echo
sudo systemctl --no-pager --full status portfolio.service
echo
echo "Installed. The Gunicorn portfolio service is enabled for every boot;"
echo "the statistics cleanup, GitHub sync, media cleanup, backup and health-check"
echo "timers are active."

if sudo test -f "$MONITOR_ENV"; then
    ALERT_URL="$(sudo sed -n 's/^NTFY_URL=//p' "$MONITOR_ENV" | tail -1)"
    if [[ -n "$ALERT_URL" ]]; then
        echo
        echo "Alerts are published to: $ALERT_URL"
        echo "Subscribe to that topic in the ntfy app (or open it in a browser)."
    fi
fi
