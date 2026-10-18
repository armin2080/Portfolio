#!/usr/bin/env bash
#
# Install the LaTeX engine used to build the CV (see portfolio_app/resume_latex.py).
#
# Tectonic rather than a full TeX Live: it is a single static binary (~26 MB),
# needs no root, and downloads only the packages a document actually uses. That
# suits the production Raspberry Pi, where a full TeX Live (hundreds of MB) is a
# poor fit for a small SD card.
#
# The binary is kept in .latex/ (git-ignored), the same arrangement as the
# Tailwind CLI in .tailwind/. The first compile also downloads the package bundle
# and caches it under the user's home by default — on a server, make that cache
# writable and set RESUME_LATEX_CACHE_DIR (see SYSTEMD_DEPLOY.md).
#
# Environment:
#   TECTONIC_VERSION   release to fetch (default below)
#   TECTONIC_BIN       path to an existing tectonic binary; skips the download
#   TECTONIC_BIN_DIR   where the downloaded binary is kept (default .latex)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${TECTONIC_VERSION:-0.17.0}"
BIN_DIR="${TECTONIC_BIN_DIR:-$ROOT/.latex}"
BIN="${TECTONIC_BIN:-$BIN_DIR/tectonic}"

case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) ASSET="x86_64-unknown-linux-musl" ;;
    Linux-aarch64 | Linux-arm64) ASSET="aarch64-unknown-linux-musl" ;;
    # 32-bit ARM: a Pi Zero running 32-bit Raspberry Pi OS reports armv6l/armv7l.
    Linux-armv6l | Linux-armv7l | Linux-arm) ASSET="arm-unknown-linux-musleabihf" ;;
    Darwin-x86_64) ASSET="x86_64-apple-darwin" ;;
    Darwin-arm64) ASSET="aarch64-apple-darwin" ;;
    *)
        echo "Unsupported platform: $(uname -s)-$(uname -m)" >&2
        echo "Set TECTONIC_BIN to a tectonic binary you installed yourself." >&2
        exit 1
        ;;
esac

if [[ -x "$BIN" ]]; then
    echo "Already installed: $("$BIN" --version)"
    exit 0
fi

URL="https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40$VERSION/tectonic-$VERSION-$ASSET.tar.gz"
echo "Downloading Tectonic $VERSION ($ASSET)..."
mkdir -p "$BIN_DIR"

# GitHub serves release assets from a shared CDN that answers 429/5xx for cloud
# and CI addresses, so retry rather than failing on a transient error.
ARCHIVE="$(mktemp -t tectonic-XXXXXX.tar.gz)"
trap 'rm -f "$ARCHIVE"' EXIT

if ! curl --fail --location --silent --show-error \
          --retry 5 --retry-delay 3 --retry-all-errors \
          --output "$ARCHIVE" "$URL"; then
    echo "Could not download $URL" >&2
    echo "Retry, or set TECTONIC_BIN to an existing tectonic binary." >&2
    exit 1
fi

tar -xzf "$ARCHIVE" -C "$BIN_DIR" tectonic
chmod +x "$BIN"

# Guard against a truncated download or an HTML error page being saved: a real
# engine reports a version, a bogus file does not.
if ! "$BIN" --version >/dev/null 2>&1; then
    rm -f "$BIN"
    echo "The downloaded file is not a working Tectonic binary: $BIN" >&2
    exit 1
fi

echo "Installed $("$BIN" --version) at $BIN"
echo
echo "The first compile also fetches the LaTeX packages the document uses."
echo "Warm that up (and the PDF cache) with:"
echo "    .venv/bin/python manage.py build_resume"
