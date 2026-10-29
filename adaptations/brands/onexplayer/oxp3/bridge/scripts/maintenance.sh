#!/bin/sh
set -eu
package=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec /usr/bin/python3 -I -B "$package/maintenance_adapter.py" "$@"
