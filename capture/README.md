# Capturing USBLinkLab traffic

The Pi capture broker runs dumpcap under its service account. The Windows operator connects over the management network. Experiment bytes use the two wired links. The Pi dashboard and Windows Wireshark can open the resulting PCAPNG files as ordinary users.

## Observation points

| Broker layer | Capture scope | Limit |
| --- | --- | --- |
| `gadget` | Configured gadget interface, Windows and Pi gadget IPv4 addresses, relay TCP listener | 1–120 seconds |
| `loopback` | Pi loopback, local iproxy TCP port | 1–120 seconds |
| `usbmon` | Selected iPhone, its current bus and address, its active USBMux interface's two bulk endpoints | 1–30 seconds, explicit private selection |

Application capture starts before the ULAB handshake. Select the gadget or loopback layer for text, image and ciphertext experiments. Optional filter expressions only narrow the broker's mandatory capture filter. The network layers' application port restrictions exclude the normal SSH management connection.

For host-side USB diagnostics, the broker matches the locally pinned Apple device, checks authorization and the active configuration, then selects exactly one interface with class/subclass/protocol `ff/fe/02`. It requires exactly one bulk OUT and one bulk IN endpoint. Missing, ambiguous or unexpected descriptors leave the USB monitor unavailable. The filter matches the current device address, URB transfer type 3 (bulk), and the two endpoint addresses. It is bound to that device's bus-specific monitor. In the observed configuration 4, the selected endpoint addresses were `04` and `85`. The device's still-image and other vendor interfaces were outside that filter. The endpoint addresses come from sysfs at capture startup, so stop before changing the phone connection or configuration. Start a new session after re-enumeration. [Linux usbmon ABI](https://www.kernel.org/doc/html/latest/usb/usbmon.html) · [usbmuxd interface selection](https://github.com/libimobiledevice/usbmuxd/blob/master/src/usb.c)

USBMux can carry several Apple services over the same endpoint pair. Use a prepared laboratory session and keep this capture private. Linux usbmon reports host-controller URB submissions and completions. Some submissions or completions carry no data. Endpoint selection alone cannot isolate port 43123 or establish application delivery. The ULAB Lua plugin expects a TCP stream such as the gadget or loopback capture.

## Windows live capture using a pinned OpenSSH host

The PowerShell launcher supplies the enrolled management host key to Windows OpenSSH and forces strict verification. Set the following variables to the existing management hostname, SSH account, local identity file, previously verified known-hosts file, and an unused output path. Use the same enrolled hostname as the known-hosts entry.

~~~powershell
& .\capture\Start-LabCapture.ps1 -ManagementHost $managementHost -User $loginName -Key $sshKey -KnownHosts $trustedHosts -Layer gadget -Seconds 30 -MiB 8 -Save $capturePath
~~~

The launcher opens Wireshark and streams the bounded binary PCAPNG to it while saving the same bytes to the requested path. Use `-NoGui` with `-Save` for an offline run. `-Filter` accepts a narrowing broker filter. A private USB diagnostic additionally requires `-Layer usbmon -PrivateUsb`. Keep plaintext and device captures under the private evidence directory. The saved file opens with Wireshark's **File → Open** or TShark's `-r`. [ULAB fields and offline extraction](../docs/ulab-dissector.md)

## Windows Wireshark's standard SSH remote capture interface

The installed Windows sshdump 1.2.0 backend advertises **SSH remote capture** with a custom remote command. Its exposed settings in this build do not include a known-hosts file or a required host-key fingerprint. A disposable local SSH fixture with a new key accepted a connection without any pre-enrolled host key. For native sshdump with explicit Pi identity verification, establish a pinned management tunnel first. An already verified OpenSSH identity and read access to the Pi broker socket are required.

In an ordinary PowerShell window, leave this command running. An unused loopback port is required. The remote forwarding destination is the Pi's own SSH service.

~~~powershell
ssh.exe -N -T -L 127.0.0.1:40222:127.0.0.1:22 -o ExitOnForwardFailure=yes -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$trustedHosts" -i $sshKey "$loginName@$managementHost"
~~~

In Wireshark's **SSH remote capture** interface options:

1. Set the remote host to `127.0.0.1` and port to `40222`.
2. Select the Pi operator account and its local SSH private key under Authentication.
3. Under Capture, choose **Other** for the remote capture command and **none** for privilege elevation.
4. Enter `python3 /opt/ulab/capture/ulab_capture.py stream --layer gadget --seconds 30 --mib 8`. Select the interface, duration and optional narrower filter in the broker command. Start capture, then use Wireshark's stop control or wait for the duration limit. Save through **File → Save As**. Close the tunnel with Ctrl+C after the session.

The same backend can save directly to a new PCAPNG. The shell variables are those used above.

~~~powershell
$sshdump = Join-Path (Split-Path (Get-Command tshark).Source) 'extcap/sshdump.exe'
& $sshdump --extcap-interfaces
& $sshdump --extcap-interface sshdump --extcap-config
if (Test-Path -LiteralPath $capturePath) { throw 'Choose an unused capture path.' }
$remoteCommand = 'python3 /opt/ulab/capture/ulab_capture.py stream --layer gadget --seconds 30 --mib 8'
& $sshdump --extcap-interface sshdump --capture --fifo $capturePath --remote-host 127.0.0.1 --remote-port 40222 --remote-username $loginName --sshkey $sshKey --remote-priv none --remote-capture-command-select other --remote-capture-command $remoteCommand
~~~

The custom-command path was exercised on Windows using a disposable localhost SSH server and a prerecorded, synthetic-content loopback PCAPNG. The server checked the complete command text, and the backend returned the same 9,268 bytes. The check covers the Windows backend's command forwarding and binary output. The two-hop native tunnel requires the Pi SSH service to permit local forwarding. The pinned launcher above works with a single SSH connection. [Wireshark sshdump manual](https://www.wireshark.org/docs/man-pages/sshdump.html)

## Retention and interpretation

Each session accepts a requested file limit of 1–32 MiB. Dumpcap checks file size at write boundaries. At most two sessions run concurrently. Before admission, the broker reclaims the oldest inactive files and sidecars until fewer than 16 files remain and existing data is at most 192 MiB. This leaves 64 MiB for two captures under a nominal 256 MiB budget. Admission requires at least 80 MiB of free space. Copy evidence you want to retain to the private evidence directory before the oldest captures rotate.

Saved captures and their JSON sidecars reside in `/var/lib/ulab-capture`. The PCAPNG contains the captured bytes. Access is restricted to the broker and the configured operator group. A manually stopped, successfully flushed file is labeled accordingly. Service startup marks abandoned in-progress sessions interrupted. A single background worker inspects completed network captures, bounded to 20,000 packets, 4 MiB of selected-field output and 18 seconds. The touch dashboard displays structurally observed frame counts and recent item metadata. A partial capture, missing handshake or exceeded budget limits the interpretation of those counts. Authentication and successful delivery require endpoint evidence; no endpoint keys enter the broker.
