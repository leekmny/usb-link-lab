#!/bin/sh
# Open the instrument on the Pi's own desktop in its own browser profile.
set -eu
profile=${XDG_CONFIG_HOME:-"$HOME/.config"}/usblinklab/chromium
install -d -m 0700 "$profile"
for browser in /usr/bin/chromium /usr/bin/chromium-browser; do
    if [ -x "$browser" ]; then
        set -- --app=http://127.0.0.1:8765/ --user-data-dir="$profile" \
            --lang=ko --disable-features=Translate --start-maximized --no-first-run --no-default-browser-check --force-light-mode
        if [ -n "${WAYLAND_DISPLAY:-}" ]; then
            set -- "$@" --ozone-platform=wayland
        fi
        exec "$browser" "$@"
    fi
done
message='Chromium is required. Install the Raspberry Pi OS chromium package.'
if command -v zenity >/dev/null 2>&1; then zenity --error --text="$message"; fi
echo "$message" >&2
exit 1
