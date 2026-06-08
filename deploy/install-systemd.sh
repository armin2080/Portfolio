#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(id -un)"
APP_GROUP="$(id -gn)"

if [[ "$PROJECT_DIR" == *" "* ]]; then
    echo "The project path must not contain spaces: $PROJECT_DIR" >&2
    exit 1
fi

echo "Installing native packages..."
sudo apt-get update
sudo apt-get install -y python3-venv

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

echo "Creating persistent data directories..."
sudo install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 /var/lib/portfolio
sudo install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 /var/lib/portfolio/staticfiles
sudo install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 /var/lib/portfolio/media

echo "Installing systemd service..."
sed \
    -e "s|__APP_USER__|$APP_USER|g" \
    -e "s|__APP_GROUP__|$APP_GROUP|g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$PROJECT_DIR/deploy/portfolio.service.template" |
    sudo tee /etc/systemd/system/portfolio.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now portfolio.service

echo
sudo systemctl --no-pager --full status portfolio.service
echo
echo "Installed. The Gunicorn portfolio service is enabled for every boot."
