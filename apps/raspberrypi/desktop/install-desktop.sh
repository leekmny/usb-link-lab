#!/bin/sh
# Run as the user who owns the Raspberry Pi desktop.
set -eu
if [ "$(id -u)" -eq 0 ]; then
    echo 'Run install-desktop.sh as the desktop user, without sudo.' >&2
    exit 1
fi
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
data=${XDG_DATA_HOME:-"$HOME/.local/share"}
desk=$(xdg-user-dir DESKTOP 2>/dev/null || true)
if [ -z "$desk" ] || [ "$desk" = "$HOME" ]; then desk="$HOME/Desktop"; fi
app="$data/usblinklab-desktop"
menu="$data/applications/ulab-dashboard.desktop"
install -d "$app" "$data/applications" "$desk"
install -m 0755 "$here/launch.sh" "$app/launch.sh"
install -m 0644 "$here/usblinklab.png" "$app/usblinklab.png"
python3 - "$here/ulab-dashboard.desktop.in" "$menu" "$app" <<'PY'
from pathlib import Path
import sys
template, menu, app = map(Path, sys.argv[1:])
if not app.is_absolute() or any(ord(c) < 32 for c in str(app)):
    raise SystemExit("Invalid XDG data directory")
def exec_quote(path):
    text = str(path).replace("%", "%%")
    text = "".join(chr(92) + c if c in ('"', chr(92), chr(96), '$') else c for c in text)
    return '"' + text.replace(chr(92), chr(92) * 2) + '"'
entry = template.read_text().replace("@LAUNCHER@", exec_quote(app / "launch.sh"))
icon = str(app / "usblinklab.png").replace(chr(92), chr(92) * 2)
menu.write_text(entry.replace("@ICON@", icon), encoding="utf-8")
PY
chmod 0644 "$menu"
desktop_file="$desk/ulab-dashboard.desktop"
install -m 0755 "$menu" "$desktop_file"
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "/run/user/$(id -u)/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
fi
if command -v gio >/dev/null 2>&1; then
    if ! gio set "$desktop_file" metadata::trusted true 2>/dev/null; then
        echo 'If the Pi asks, choose Allow Launching on the desktop icon.' >&2
    fi
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$data/applications"
fi
printf '%s\n' "Menu: $menu" "Desktop: $desktop_file"
