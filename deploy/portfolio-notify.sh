#!/usr/bin/env bash
# Send a short alert and always leave it in the journal.
#
# Usage: portfolio-notify "Title" "Body"
#
# Delivery is configured in /etc/portfolio-monitor.env. If NTFY_URL is set, the
# message is published to that ntfy topic (https://ntfy.sh/<topic>, or a
# self-hosted server). Without it the alert is only written to the journal,
# which still makes it visible in `journalctl -u portfolio-alert`.
#
# The journal copy comes first on purpose: a notification service being down
# must never lose the record of what happened.
set -uo pipefail

TITLE="${1:-Portfolio alert}"
BODY="${2:-}"
MONITOR_ENV="${PORTFOLIO_MONITOR_ENV:-/etc/portfolio-monitor.env}"

if [[ -r "$MONITOR_ENV" ]]; then
    # shellcheck disable=SC1090
    set -a
    . "$MONITOR_ENV"
    set +a
fi

# Header values must be a single line.
TITLE="${TITLE//$'\n'/ }"
BODY="${BODY//$'\r'/}"

printf 'ALERT: %s\n%s\n' "$TITLE" "$BODY" >&2

if [[ -z "${NTFY_URL:-}" ]]; then
    exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "portfolio-notify: curl is not installed; alert was only logged." >&2
    exit 1
fi

if ! curl --silent --show-error --fail --max-time 15 \
    -H "Title: $TITLE" \
    -H "Priority: high" \
    -H "Tags: rotating_light" \
    --data-binary "$BODY" \
    "$NTFY_URL" >/dev/null; then
    echo "portfolio-notify: could not publish to NTFY_URL." >&2
    exit 1
fi
