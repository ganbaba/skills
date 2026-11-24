#!/bin/sh
set -eu
HANDOFF_INSTALL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$HANDOFF_INSTALL_DIR/install.py" "$@"
