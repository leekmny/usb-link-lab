#!/usr/bin/env bash
# Install the capture broker and loopback dashboard for one local Pi desktop owner.
set -euo pipefail
operator=${1:-${SUDO_USER:-}}
[[ $EUID == 0 && "$operator" =~ ^[a-z_][a-z0-9_-]*$ && "$operator" != root ]]
getent passwd "$operator" >/dev/null
group=$(id -gn "$operator")
# This dedicated lab's desktop uses a private primary group. Reject a shared one.
[[ $group == "$operator" ]] || { echo "A private primary group is required for local capture access." >&2; exit 2; }
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends tshark wireshark fonts-noto-cjk acl
getent passwd ulab-capture >/dev/null || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin ulab-capture
install -d -m 0755 /opt/ulab/{capture,dissectors} /etc/systemd/system/ulab-capture.service.d
install -m 0644 "$repo/capture/ulab_capture.py" /opt/ulab/capture/
install -m 0644 "$repo/apps/raspberrypi/"dashboard.{py,html} /opt/ulab/
install -m 0644 "$repo/dissectors/ulab.lua" /opt/ulab/dissectors/
install -m 0644 "$repo/capture/ulab-capture.service" /etc/systemd/system/
printf '[Service]\nGroup=%s\n' "$group" > /etc/systemd/system/ulab-capture.service.d/operator.conf
if [[ -r "$repo/REVISION" ]]; then
    install -m 0644 "$repo/REVISION" /opt/ulab/capture/REVISION
elif git -C "$repo" rev-parse HEAD >/dev/null 2>&1; then
    git -C "$repo" rev-parse HEAD > /opt/ulab/capture/REVISION
fi
install -d -m 0755 /etc/systemd/user
install -m 0644 "$repo/apps/raspberrypi/systemd/ulab-dashboard.service" /etc/systemd/user/
if [[ -s /etc/ulab/iphone.udid ]]; then
    install -m 0640 -o root -g ulab-capture /etc/ulab/iphone.udid /etc/ulab/capture-device
    # Owner-specific ACL also works with the service's desktop group override.
    setfacl -m u:ulab-capture:r /etc/ulab/capture-device
fi
if modprobe usbmon; then
    # Only this non-login service user gets read access. The broker matches the selected device's current bus/address.
    printf '%s\n' 'usbmon' > /etc/modules-load.d/ulab-usbmon.conf
    printf '%s\n' 'SUBSYSTEM=="usbmon", KERNEL=="usbmon[0-9]*", RUN+="/usr/bin/setfacl -m u:ulab-capture:r /dev/%k"' > /etc/udev/rules.d/75-ulab-usbmon.rules
    udevadm control --reload
    for dev in /dev/usbmon[0-9]*; do [[ ! -e "$dev" ]] || setfacl -m u:ulab-capture:r "$dev"; done
fi
systemctl daemon-reload
systemctl enable --now ulab-capture.service
systemctl restart ulab-capture.service
echo "As the desktop user run: systemctl --user daemon-reload && systemctl --user enable --now ulab-dashboard"
