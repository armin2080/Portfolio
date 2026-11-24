# Monitoring and backups

This describes what actually runs on the server, how to look at it, and how to
restore from a backup. It is deliberately small: one health URL, one periodic
check, one alert channel and one daily backup.

## What runs

| Piece | Schedule | What it does |
| --- | --- | --- |
| `/healthz/` | On request | Checks the database and cache; reports whether a LaTeX engine is available. `200` with `"status": "ok"`, or `503`. |
| `portfolio-watchdog.timer` | Every 5 minutes | Checks the service, `/healthz/`, disk space, available memory, SMART health and backup age. Alerts only when something is wrong. |
| `portfolio-alert.service` | On failure | Sends one alert when systemd gives up on `portfolio.service` or a maintenance unit fails. |
| `portfolio-backup.timer` | Daily at 02:30 | Writes `/var/backups/portfolio/portfolio-YYYYmmdd-HHMMSS.tar.gz`, keeps 14 days. |
| `smartd` | Continuously | Watches disk SMART data; the watchdog also runs `smartctl -H` on each disk. |

The existing timers for statistics cleanup, GitHub sync and media pruning are
unchanged. `systemctl list-timers 'portfolio-*'` shows all of them.

## How to see it

```bash
# Is the service healthy?
systemctl status portfolio
curl -s http://127.0.0.1/healthz/ \
  -H "Host: $(sed -n 's/^SITE_URL=//p' .env | sed -E 's#^https?://##; s#/.*$##')" \
  -H 'X-Forwarded-Proto: https'

# What have the checks and alerts been doing?
systemctl list-timers 'portfolio-*' --all
journalctl -u portfolio-watchdog -n 50 --no-pager
journalctl -u portfolio-alert -n 50 --no-pager
systemctl --failed

# Backups
ls -lh /var/backups/portfolio/
cat /var/backups/portfolio/last-success

# Disk health
sudo smartctl -H /dev/sda        # replace with the disk from lsblk
```

The private `/dashboard/` is visitor analytics, not health monitoring. It tells
you who visited, not whether the service is up.

## Alerts

`deploy/install-systemd.sh` creates `/etc/portfolio-monitor.env` (mode 0600)
with a new random [ntfy](https://ntfy.sh) topic. The installer prints the full
topic URL; subscribe to it in the ntfy app, or open the URL in a browser.

```bash
sudo grep '^NTFY_URL=' /etc/portfolio-monitor.env
```

Send a test message:

```bash
sudo /usr/local/bin/portfolio-notify "Test alert" "Monitoring is working."
```

To use a different topic, your own ntfy server, or to disable push alerts:

```bash
sudoedit /etc/portfolio-monitor.env
```

The watchdog reads that file on every run. With `NTFY_URL` empty, alerts are
still written to the journal (`journalctl -u portfolio-alert`), just not pushed
to a phone.

Alerts are rate-limited: a persistent problem is re-sent at most once an hour
(`REPEAT_ALERT_SECONDS`), and immediately when the set of problems changes.

## External uptime monitor

The watchdog runs on the same machine, so it cannot report a power cut, a dead
disk or a lost network connection. Only an external checker catches those.

1. Create a free account at [UptimeRobot](https://uptimerobot.com) (or Better
   Stack, or any monitor that supports HTTPS and keyword matching).
2. Add a monitor:
   - Type: **HTTPS**
   - URL: `https://armin2080.de/healthz/`
   - Interval: 5 minutes
   - Alert after 2–3 failed checks
   - Keyword (optional but recommended): `"status": "ok"` — this also catches a
     `200` page that is not actually this application.
3. Attach your email as the alert contact and press **Test Notification**.
4. If Cloudflare is in front of the origin, make sure no WAF rule, rate limit or
   "Under Attack" challenge blocks the monitor's requests to `/healthz/`.

`/healthz/` is excluded from the visitor statistics and disallowed in
`robots.txt`.

## Thresholds

Everything is in `/etc/portfolio-monitor.env`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `DISK_MAX_PERCENT` | `90` | Alert when a checked filesystem is fuller than this. |
| `MEMORY_MIN_MB` | `128` | Alert when `MemAvailable` drops below this. |
| `BACKUP_MAX_AGE_HOURS` | `36` | Alert when the last successful backup is older. |
| `DISK_PATHS` | `/ /var/lib/portfolio` | Filesystems to check. |
| `HEALTH_HOST` | site host from `.env` | Host header for the loopback probe. Must be in `ALLOWED_HOSTS`. |

If you change `BACKUP_DIR` in `.env`, change it here too, and add the new path
to `ReadWritePaths=` in `deploy/portfolio-backup.service.template`.

## Backups

`manage.py backup_portfolio` copies the database through SQLite's backup API
(not by reading a live file), then archives it together with uploaded media and
`.env`:

```bash
.venv/bin/python manage.py backup_portfolio --dry-run   # show what would happen
.venv/bin/python manage.py backup_portfolio             # write one now
```

The archive is written `0600` inside a `0700` directory. **It contains `.env`,
so it contains the site's secrets** — treat copies accordingly.

By default it lands on the same disk as the site. That protects against
accidental deletion and database corruption, but not against disk failure. If
you have another mounted disk or a synced folder, set `BACKUP_DIR` there and
update the backup unit's `ReadWritePaths=`.

### Restore

```bash
sudo systemctl stop portfolio

# Database and media go back to the data directory.
sudo tar xzf /var/backups/portfolio/portfolio-YYYYmmdd-HHMMSS.tar.gz \
  -C /var/lib/portfolio db.sqlite3 media

# .env goes back to the project directory (skip if you kept the current one).
sudo tar xzf /var/backups/portfolio/portfolio-YYYYmmdd-HHMMSS.tar.gz \
  -C /path/to/Portfolio .env

sudo chown -R "$(id -un):$(id -gn)" /var/lib/portfolio
sudo systemctl start portfolio
curl -fsS https://armin2080.de/healthz/
```

List an archive without extracting it with `tar tzf <archive>`.

## What this does not do

- No metrics history or dashboards. The journal is the record.
- No off-site copy unless `BACKUP_DIR` is moved to another disk or synced.
- No log shipping or central log aggregation.
- No intrusion detection. Cloudflare's firewall and the application's own rate
  limits remain the front line.
