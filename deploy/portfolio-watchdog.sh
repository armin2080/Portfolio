#!/usr/bin/env bash
# Periodic health check for the portfolio host.
#
# Runs from portfolio-watchdog.timer every five minutes and alerts through
# portfolio-notify when something a visitor would notice is wrong: the service
# is down, the health endpoint fails, disk or memory is nearly exhausted, a
# disk reports a SMART failure, or backups have stopped running.
#
# Configuration lives in /etc/portfolio-monitor.env. Everything has a sane
# default, so the script works before that file exists.
set -uo pipefail

MONITOR_ENV="${PORTFOLIO_MONITOR_ENV:-/etc/portfolio-monitor.env}"
if [[ -r "$MONITOR_ENV" ]]; then
    # shellcheck disable=SC1090
    set -a
    . "$MONITOR_ENV"
    set +a
fi

NOTIFY="${PORTFOLIO_NOTIFY:-/usr/local/bin/portfolio-notify}"
HEALTH_HOST="${HEALTH_HOST:-localhost}"
HEALTH_PORT="${HEALTH_PORT:-80}"
DISK_MAX_PERCENT="${DISK_MAX_PERCENT:-90}"
MEMORY_MIN_MB="${MEMORY_MIN_MB:-128}"
DISK_PATHS="${DISK_PATHS:-/ /var/lib/portfolio}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/portfolio}"
BACKUP_MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-36}"
BACKUP_REQUIRED="${BACKUP_REQUIRED:-1}"
REPEAT_ALERT_SECONDS="${REPEAT_ALERT_SECONDS:-3600}"
STATE_FILE="${WATCHDOG_STATE_FILE:-/run/portfolio-watchdog.state}"
HOSTNAME_SHORT="$(hostname 2>/dev/null || echo server)"

problems=()

# --- service ---------------------------------------------------------------
if ! systemctl is-active --quiet portfolio.service; then
    state="$(systemctl is-active portfolio.service 2>/dev/null || true)"
    problems+=("portfolio.service is ${state:-not active}")
fi

# --- HTTP health endpoint --------------------------------------------------
# The request is made to loopback, like Cloudflare does, and carries the same
# forwarding header so SECURE_SSL_REDIRECT does not turn it into a 301.
if command -v curl >/dev/null 2>&1; then
    response="$(curl --silent --show-error --max-time 10 \
        -H "Host: $HEALTH_HOST" \
        -H 'X-Forwarded-Proto: https' \
        -w $'\n%{http_code}' \
        "http://127.0.0.1:${HEALTH_PORT}/healthz/" 2>/dev/null || true)"
    code="${response##*$'\n'}"
    body="${response%$'\n'*}"
    if [[ "$code" != "200" || "$body" != *'"status": "ok"'* ]]; then
        problems+=("health probe failed (HTTP ${code:-no response})")
    fi
else
    problems+=("curl is not installed, cannot probe /healthz/")
fi

# --- disk space ------------------------------------------------------------
if command -v df >/dev/null 2>&1; then
    for path in $DISK_PATHS; do
        [[ -e "$path" ]] || continue
        percent="$(df -P "$path" | awk 'NR==2 {gsub("%", "", $5); print $5}')"
        if [[ -n "$percent" ]] && (( percent > DISK_MAX_PERCENT )); then
            problems+=("disk $path is ${percent}% full")
        fi
    done
fi

# --- memory ----------------------------------------------------------------
if [[ -r /proc/meminfo ]]; then
    available_mb="$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo)"
    if [[ -n "$available_mb" ]] && (( available_mb < MEMORY_MIN_MB )); then
        problems+=("only ${available_mb} MiB memory available")
    fi
fi

# --- SMART -----------------------------------------------------------------
if command -v smartctl >/dev/null 2>&1 && command -v lsblk >/dev/null 2>&1; then
    while read -r name type; do
        [[ "$type" == "disk" ]] || continue
        case "$name" in
            loop*|ram*|zram*|sr*) continue ;;
        esac
        health="$(smartctl -H "/dev/$name" 2>&1 || true)"
        if grep -qiE 'FAILED!|NOT PASSED' <<<"$health"; then
            problems+=("SMART reports a failing disk: /dev/$name")
        fi
    done < <(lsblk -dno NAME,TYPE 2>/dev/null)
fi

# --- backup freshness ------------------------------------------------------
if [[ -e "$BACKUP_DIR/last-success" ]]; then
    last_backup="$(stat -c %Y "$BACKUP_DIR/last-success" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    age_hours=$(( (now - last_backup) / 3600 ))
    if (( age_hours > BACKUP_MAX_AGE_HOURS )); then
        problems+=("last successful backup was ${age_hours}h ago")
    fi
elif [[ "$BACKUP_REQUIRED" == "1" ]]; then
    problems+=("no successful backup found in $BACKUP_DIR")
fi

# --- report ----------------------------------------------------------------
if (( ${#problems[@]} == 0 )); then
    rm -f "$STATE_FILE"
    exit 0
fi

body="$(printf -- '- %s\n' "${problems[@]}")"
fingerprint="$(printf '%s\n' "$body" | sha256sum | cut -d' ' -f1)"
now="$(date +%s)"
last_sent=0
last_fingerprint=''
if [[ -r "$STATE_FILE" ]]; then
    read -r last_sent last_fingerprint <"$STATE_FILE" || true
fi

# The same problem would otherwise arrive every five minutes. Re-alert once an
# hour, and immediately whenever the set of problems changes.
if [[ "$fingerprint" != "$last_fingerprint" ]] \
    || (( now - ${last_sent:-0} >= REPEAT_ALERT_SECONDS )); then
    "$NOTIFY" \
        "Portfolio on $HOSTNAME_SHORT: ${#problems[@]} problem(s)" \
        "$body"
    printf '%s %s\n' "$now" "$fingerprint" >"$STATE_FILE"
fi

exit 1
