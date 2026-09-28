#!/usr/bin/env bash
#
# Build the production Tailwind CSS bundle.
#
# Uses the standalone Tailwind CLI so Node.js is not required (handy on a
# Raspberry Pi). The binary is downloaded once into .tailwind/ (git-ignored)
# and the compiled CSS is written to static/css/tailwind.css.
#
# Environment:
#   TAILWIND_VERSION   CLI version to fetch (default below)
#   TAILWIND_BIN       path to an existing tailwindcss binary; skips the download
#   TAILWIND_BIN_DIR   where the downloaded binary is kept (default .tailwind)
#
# Usage:
#   scripts/build-css.sh          # one-off minified build
#   scripts/build-css.sh watch    # rebuild on change (development)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${TAILWIND_VERSION:-3.4.17}"
BIN_DIR="${TAILWIND_BIN_DIR:-$ROOT/.tailwind}"
BIN="${TAILWIND_BIN:-$BIN_DIR/tailwindcss}"

case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) ASSET="tailwindcss-linux-x64" ;;
    Linux-aarch64 | Linux-arm64) ASSET="tailwindcss-linux-arm64" ;;
    Darwin-x86_64) ASSET="tailwindcss-macos-x64" ;;
    Darwin-arm64) ASSET="tailwindcss-macos-arm64" ;;
    *)
        echo "Unsupported platform: $(uname -s)-$(uname -m)" >&2
        exit 1
        ;;
esac

if [[ ! -x "$BIN" ]]; then
    URL="https://github.com/tailwindlabs/tailwindcss/releases/download/v$VERSION/$ASSET"
    echo "Downloading Tailwind CSS v$VERSION ($ASSET)..."
    mkdir -p "$BIN_DIR"

    # GitHub serves release assets from a shared CDN and can answer 429/5xx for
    # requests from cloud/CI IP addresses, so retry rather than failing the
    # build on a transient error (this is what broke the first CI run).
    if ! curl --fail --location --silent --show-error \
              --retry 5 --retry-delay 3 --retry-all-errors \
              --output "$BIN" "$URL"; then
        rm -f "$BIN"
        echo "Could not download $URL" >&2
        echo "Retry, or point TAILWIND_BIN at an existing tailwindcss binary." >&2
        exit 1
    fi

    chmod +x "$BIN"

    # Guard against a truncated download or an HTML error page being saved:
    # a real CLI exits 0 for --help, a bogus file does not.
    if ! "$BIN" --help >/dev/null 2>&1; then
        rm -f "$BIN"
        echo "The downloaded file is not a working Tailwind CLI: $BIN" >&2
        exit 1
    fi
fi

MODE="--minify"
if [[ "${1:-}" == "watch" ]]; then
    MODE="--watch"
fi

"$BIN" \
    --config "$ROOT/tailwind.config.js" \
    --input "$ROOT/static/css/tailwind.src.css" \
    --output "$ROOT/static/css/tailwind.css" \
    $MODE

echo "Built static/css/tailwind.css"
