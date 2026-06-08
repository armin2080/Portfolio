# Raspberry Pi systemd deployment

This deployment runs Django directly with Gunicorn on port 80. It uses SQLite
at `/var/lib/portfolio/db.sqlite3`, making it suitable for a Raspberry Pi Zero
2 W. WhiteNoise serves static files, while Django serves the small amount of
uploaded media used by this portfolio.

## Install

Run this as the normal Linux user that owns the project:

```bash
chmod +x deploy/install-systemd.sh
./deploy/install-systemd.sh
```

The script:

- installs `python3-venv`;
- creates `.venv` and installs Python dependencies;
- creates `.env` from `.env.example` when needed;
- stores persistent data in `/var/lib/portfolio`;
- installs and starts `portfolio.service`;
- enables Portfolio to start after every reboot.

After installation, review `.env`, especially `ALLOWED_HOSTS` and email
credentials, then restart the app:

```bash
sudo systemctl restart portfolio
```

## Operations

```bash
# Status and recent logs
systemctl status portfolio
journalctl -u portfolio -n 100 --no-pager

# Follow logs
journalctl -u portfolio -f

# Restart after changing code or .env
sudo systemctl restart portfolio

# Verify the service is enabled at boot
systemctl is-enabled portfolio
```

For an application update:

```bash
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart portfolio
```

The service runs migrations and collects static files before each start. If
either operation fails, inspect `journalctl -u portfolio` and fix the error;
systemd will keep retrying failed application starts.
