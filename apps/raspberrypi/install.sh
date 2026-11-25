#!/usr/bin/env bash
# Install relay tools and units on Raspberry Pi OS. Configure separately.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo 'Use sudo bash apps/raspberrypi/install.sh' >&2
    exit 1
fi
if [[ ! -r /proc/device-tree/model ]] || ! grep -aq 'Raspberry Pi' /proc/device-tree/model; then
    echo 'Raspberry Pi OS hardware is required for deployment.' >&2
    exit 1
fi

here=$(cd -- "$(dirname -- "$0")" && pwd)
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 iproute2 usbutils usbmuxd \
    libimobiledevice-utils libusbmuxd-tools tshark wireshark-common tcpdump

install -d -m 0755 /opt/ulab /etc/ulab
for file in relay.py gadget.py configure.py usb_proxy.py; do
    install -o root -g root -m 0644 "$here/$file" "/opt/ulab/$file.new"
    mv -f -- "/opt/ulab/$file.new" "/opt/ulab/$file"
done
for unit in ulab-iproxy.service ulab-relay.service; do
    install -o root -g root -m 0644 "$here/systemd/$unit" "/etc/systemd/system/$unit.new"
    mv -f -- "/etc/systemd/system/$unit.new" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
dpkg-query -W -f='${Package} ${Version}\n' python3 usbmuxd libimobiledevice-utils \
    libusbmuxd-tools tshark wireshark-common tcpdump
echo 'Run sudo python3 /opt/ulab/configure.py --enable on the Pi to bind a wired lab pair.'
