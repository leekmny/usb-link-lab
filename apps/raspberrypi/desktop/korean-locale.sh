#!/bin/sh
# Apply the Korean interface at next login, leaving keyboard layout and current session intact.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run using sudo on the Pi.' >&2; exit 2; }
account=${SUDO_USER:-}
[ -n "$account" ] && [ "$account" != root ] || { echo 'Run from the desktop user account.' >&2; exit 2; }
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends locales fonts-noto-cjk
if ! locale -a | grep -iq '^ko_KR\.utf8$'; then
    sed -i -E 's/^[[:space:]]*#[[:space:]]*(ko_KR.UTF-8 UTF-8)[[:space:]]*$/\1/' /etc/locale.gen
    grep -qxF 'ko_KR.UTF-8 UTF-8' /etc/locale.gen || printf '\nko_KR.UTF-8 UTF-8\n' >> /etc/locale.gen
    locale-gen
fi
update-locale LANG=ko_KR.UTF-8 LANGUAGE=ko_KR:ko
if command -v busctl >/dev/null && busctl --system --list | grep -q org.freedesktop.Accounts; then
    uid=$(id -u "$account")
    busctl --system call org.freedesktop.Accounts "/org/freedesktop/Accounts/User$uid" org.freedesktop.Accounts.User SetLanguage s ko_KR.UTF-8
fi
fc-cache -f
locale -a | grep -i '^ko_KR\.utf8$'
fc-match -f '%{family}\n' 'sans-serif:lang=ko'
