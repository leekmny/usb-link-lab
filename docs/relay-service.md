# Raspberry Pi byte relay

The Pi accepts one Windows TCP stream over the USB gadget and forwards its ordered bytes to one IPv4 loopback connection to iproxy. iproxy selects the configured iPhone through the local usbmuxd socket and opens its existing loopback application port. Windows remains the ULAB client and iPhone remains the ULAB server throughout enrollment, transfers and DRAIN. The Pi holds no ULAB keys.

## Install on the Pi

Keep an independent SSH session over Wi-Fi/Ethernet during setup. Attach the lab iPhone to the Pi's USB-A host port and use a documented power/data splitter on the gadget USB-C port. Verify the external power and splitter rating before attaching a load. Use the official Raspberry Pi gadget configuration. If provisioning a new Pi, first verify that the correct gadget software and Windows RNDIS driver are installed. Enabling a gadget or rebooting is a separate hardware setup step. [Official gadget project](https://github.com/raspberrypi/rpi-usb-gadget)

Run from the integrated repository or source bundle, using Linux line endings.

```bash
sudo bash apps/raspberrypi/install.sh
ip -br -4 addr
for d in /sys/class/net/*; do
    if grep -qx DEVTYPE=gadget "$d/uevent"; then
        echo "$d"
        cat "$d/carrier"
    fi
done
vcgencmd get_throttled
sudo python3 /opt/ulab/configure.py --pair --enable
```

The configuration command displays candidate gadget interfaces and local addresses. Enter the Windows USB adapter IPv4 address shown by Windows `Get-NetIPAddress -AddressFamily IPv4`. If several USB phones are present, it prompts for an explicit selection. Unlock the lab iPhone and approve the Apple **Trust This Computer** prompt. If validation reports pending trust, keep the phone unlocked and repeat the configuration. Keep the ULAB app in the foreground. Prepare a QR or PIN invitation in the app for first enrollment. A saved peer can reconnect using its existing endpoint credentials.

For unattended selection with known USB interface and addresses, set the three shell variables locally and run the following. `--only-usb-device` rejects zero or multiple USB devices. Omitting `--pair` only validates pre-existing Apple host trust.

```bash
sudo python3 /opt/ulab/configure.py --interface "$GADGET_IF" \
    --listen-address "$PI_USB_IP" --windows-address "$WINDOWS_USB_IP" \
    --only-usb-device --enable
```

The installer places Python files in `/opt/ulab`, installs system units and runs `daemon-reload`. It uses the distribution's dependencies. The separate configuration command writes the selected UDID to root-only `/etc/ulab/iphone.udid` (0600) and gadget addresses and ports to `/etc/ulab/relay.json`. Unit activation occurs with `--enable`. Choose distinct unprivileged ports with `--listen-port` and `--upstream-port` if the default 43123 and 43124 are already used. The iPhone application port is always 43123. Rerun configuration after a DHCP change affecting either configured address.

## Runtime checks

```bash
systemctl is-active ulab-relay ulab-iproxy
systemctl is-enabled ulab-relay
cat /run/ulab-relay/status.json
ss -ltn
journalctl -u ulab-relay.service --since '10 minutes ago' -o cat
```

A running Pi exposes only the selected gadget IPv4 and port on the front end and a 127.0.0.1 listener for iproxy. Do not probe port 43123 to check service readiness. A TCP connection consumes the iPhone app's single listener session and can disrupt enrollment.

- Both units run with separate dynamic OS identities and an empty capability set. The proxy reads the device identifier through a systemd credential. The relay checks that the configured upstream port has a loopback LISTEN socket owned by the iproxy identity. The installed iproxy help must support explicit USB-only discovery, a specific device ID and a loopback bind address. The launcher gives iproxy a clean environment, without usbmux endpoint overrides. Its child output is suppressed to keep the identifier out of routine logs. Administrators can still inspect its live command arguments locally. Treat the Pi and local OS users as trusted for physical access. [iproxy manual](https://github.com/libimobiledevice/libusbmuxd/blob/master/docs/iproxy.1)
- The listener verifies Linux `DEVTYPE=gadget`, carrier and the addresses' common on-link subnet, binds to the gadget using `SO_BINDTODEVICE`, and filters incoming peers by the configured Windows IP. It rechecks the link and configured subnet every two seconds. If these checks fail, it closes the paired stream and systemd retries the service. Endpoint authentication still establishes ULAB peer identity.
- A single session uses two independent read/write tasks with 64 KiB blocks and backpressure. Only stalled writes have a 60-second progress deadline. A FIN propagates as a TCP half-close; the reverse stream has up to ten seconds to finish already-pending final bytes. A fresh client after cleanup starts a fresh upstream connection. The relay never replays bytes into the new session. Endpoint timeouts, control messages, acknowledgments and retransmission decisions stay at the endpoints.
- The atomic status JSON updates every two seconds and is world-readable on this trusted Pi. It includes the UTC sampling time, current USB listener, active-session count, and per-process connection and directional byte totals. Byte counts reflect bytes submitted to each transport. The service does not identify application modes, authenticate packets, or confirm delivery. Session end logs give a local random identifier, byte counts, a time and a sanitized transport outcome. A restart resets cumulative counters. Use Wireshark for per-frame observations and endpoint results for delivery evidence.

## Recovery and validation

If the gadget disappears, retain the management SSH session and check the cable, `ip -br -4 addr`, `/sys/class/net/*/uevent`, carrier and the current addresses. For repeated `upstream_unavailable`, verify both configured ports, iproxy service state, Linux loopback listener ownership, and Apple host trust. A running proxy does not prove that an invited foreground ULAB app is reachable. A closed iPhone app or a pending invitation must be handled at the endpoint.

Restart the pair with `sudo systemctl restart ulab-relay.service`. To revert service activation run `sudo bash apps/raspberrypi/rollback.sh`. It disables the relay at boot and stops both units. The previous gadget, SSH access, distribution packages, Apple host trust and ULAB endpoint data are preserved. Re-enable the validated configuration with `sudo systemctl enable --now ulab-relay.service`.

Run the portable relay tests on Python 3.11 or later from the repository root.

```bash
python3 -m unittest discover -s tests -p 'test_relay*.py' -v
```

These tests use real TCP sockets on loopback. Physical acceptance additionally requires Windows-to-iPhone delivery and the reverse, route and capture evidence for both USB legs, a safe interruption of each data link, and recovery across a real reboot. Preserve the management connection during the disconnect tests. Record hardware and power readings during active transfer.
