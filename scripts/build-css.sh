#!/usr/bin/env bash
#
# Build the production Tailwind CSS bundle.
#
# Uses the standalone Tailwind CLI so Node.js is not required (handy on a
# Raspberry Pi). The binary is downloaded once into .tailwind/ (git-ignored)
# and the compiled CSS is written to static/css/tailwind.css.
#
# Usage:
#   scripts/build-css.sh          # one-off minified build
#   scripts/build-css.sh watch    # rebuild on change (development)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${TAILWIND_VERSION:-3.4.17}"
BIN_DIR="$ROOT/.tailwind"
BIN="$BIN_DIR/tailwindcss"

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
    echo "Downloading Tailwind CSS v$VERSION ($ASSET)..."
    mkdir -p "$BIN_DIR"
    curl -fsSL -o "$BIN" \
        "https://github.com/tailwindlabs/tailwindcss/releases/download/v$VERSION/$ASSET"
    chmod +x "$BIN"
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
