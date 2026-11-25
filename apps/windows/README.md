# USBLinkLab for Windows development

Windows 11 x64 is the target. Qt Widgets uses PySide6 Essentials. The current
build pins CPython 3.12.16, uv 0.13.8 in CI, PySide6 Essentials 6.10.2,
cryptography 50.0.0, pytest 9.0.2, and PyInstaller 6.19.0. The Windows wheel
lock includes exact SHA-256 hashes.

Run these commands from the repository root in x64 PowerShell with uv and Git available.

~~~powershell
.\apps\windows\windows.ps1 setup
.\apps\windows\windows.ps1 test
.\apps\windows\windows.ps1 run
.\apps\windows\windows.ps1 package
.\apps\windows\windows.ps1 smoke
~~~

Package creates apps/windows/dist/release/USBLinkLab-windows-x64.zip and
SHA256SUMS. The ZIP contains the application, bundled native runtime, protocol
revision, source-commit metadata, executable signature status, and third-party
notices. Extract the ZIP and keep the directory together. Rebuild with
the same versions and the same source commit for a comparable package.
ZIP timestamps can vary between equivalent builds. The standalone app
uses the system [Microsoft Visual C++ x64 Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).

Windows CI builds on windows-2025 and uploads the ZIP and checksum with a
14-day retention period. A separate runner extracts and launches the ZIP with
system-only PATH. An intentional existing windows-vX.Y.Z tag and the Windows
prerelease workflow can publish the tested ZIP and checksum. The release job
checks the source commit before publication and downloads the assets again to
verify their contents.

## Apple USB prerequisites and probe

These prerequisites apply to **Direct USB to iPhone**, the default connection path.

Install Apple Devices separately, attach an unlocked iPhone with a data-capable
cable, approve the phone's Trust This Computer prompt when shown, and confirm
the phone appears in Apple Devices. If you attached the phone to WSL with
usbipd, detach it in PowerShell first. Keep the companion app foregrounded at
its configured listener on IPv4 loopback port 43123. [Apple USB troubleshooting](https://support.apple.com/en-us/108643), [Apple Mobile Device Service](https://support.apple.com/en-us/102347), [Microsoft WSL USB guide](https://learn.microsoft.com/en-us/windows/wsl/connect-usb).

~~~powershell
.\apps\windows\windows.ps1 probe
.\apps\windows\windows.ps1 probe -ShowAliases
.\apps\windows\windows.ps1 probe -ConnectAlias 'USB device <alias from the previous command>'
~~~

The Windows client implements the subset of the usbmux v1 plist protocol needed
for ListDevices and Connect in-process. It verifies the Windows service PID
and the server endpoint of the actual connection to 127.0.0.1:27015 before
sending commands. It rechecks the explicitly selected USB device and numeric
handle on that same control socket before connecting to the single app port.
This design uses the Apple service directly and keeps the portable bundle free
of an iproxy executable, subprocess, and additional listening port.
See the [libusbmuxd 2.1.1 wire definitions](https://github.com/libimobiledevice/libusbmuxd/blob/2.1.1/include/usbmuxd-proto.h), [client implementation](https://github.com/libimobiledevice/libusbmuxd/blob/2.1.1/src/libusbmuxd.c), [Microsoft service PID API](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-queryservicestatusex), and [TCP endpoint owner API](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable).

| Observation | Meaning |
| --- | --- |
| service_missing | Service Control Manager returned service name absent. |
| service_stopped | Service Control Manager returned the stopped state. |
| mux_owner_mismatch | The local multiplexer socket could not be attributed to the running Apple service. A conflicting listener is one possible cause. |
| no_usb_device | Apple returned no eligible USB records. Check the cable, lock/trust state, driver, and WSL attachment. |
| multiple_usb_devices | Select the correct device alias. |
| listener_refused | Apple returned connection refused for the one app port. Foreground readiness, the port in the companion build, and Apple trust restrictions are possible causes. |
| usb_stream_open | The selected USB connection to the iPhone app port opened. The probe closes the stream after this observation. |

The initial probe exports a device count and sanitized result codes. Use
ShowAliases locally to identify a selection. The application's connection
status covers its separate authentication and protocol checks. A successful
application handshake still requires the actual cable test to establish the
physical result.

## Clear transfer history

In Transfer history, choose Clear history to delete the eligible records shown in the list, or choose Select items for a subset. The confirmation shows the count. Deleting an item removes its record and the app-owned copy of its message or attachment. Completed sent and received items, paused outgoing items, and records left by an interrupted session can be removed. Active and queued items remain available for transfer until their status changes.

To remove one received text message from its details, choose Delete message and confirm.

## Reconnect over USB

After an authenticated session drops, the Windows app automatically retries the same paired iPhone with increasing delays of up to 8 seconds between attempts. Keep the iPhone app open and the device unlocked for a new USB session. Use Disconnect or Cancel session to stop automatic retries. Interrupted or unconfirmed transfers require an explicit retry.

## Raspberry Pi USB relay on Windows

1. Connect the Pi gadget to Windows and the iPhone to the Pi's USB-A port. On the Pi, confirm the gadget IPv4 address and the port configured for the relay. Keep the iPhone unlocked with USBLinkLab in the foreground.
2. Open **설정 / Settings** in Windows. Choose **Raspberry Pi USB 중계 / Raspberry Pi USB relay**, enter the Pi's **gadget IPv4 address** and the relay port (default **43123**). Choose **적용 / Apply**. The discovery step reads local Windows routing and adapter state without opening the iPhone application port.
3. Choose the discovered adapter in the device selector. Its entry shows the Windows source and Pi destination. The diagnostics panel also shows the port. A verified wired route enables the next step. Only the authenticated ULAB handshake changes the status to **iPhone app authenticated**.
4. To use the same iPhone that already authenticates in direct mode, enable **Use the saved direct iPhone pairing** and choose **Connect**. This permits only saved-key authentication. Existing queued jobs keep their original peer and secret binding. The worker resumes eligible items only after that peer completes the handshake. Interrupted or unconfirmed items still require explicit retry.
5. For independent enrollment, leave that option clear and use **Pair**. QR and six-digit enrollment use the existing endpoint flows. The new pairing occupies a separate current-user DPAPI slot and preserves the direct Windows credential and queue. An iPhone with a single saved credential can replace its previous credential during re-enrollment. Use the saved direct pairing option when you want to preserve access to the existing iPhone session profile.

Pi mode requires the Raspberry Pi USB network driver and Windows PowerShell networking cmdlets. It runs independently of Apple's Windows service. The application accepts numeric IPv4 destinations on the directly attached, active Raspberry Pi gadget (USB VID 2E8A, PID 0013). It checks the system-selected address, hardware identity and on-link route before dialing, pins the socket with `IP_UNICAST_IF`, binds the verified source IPv4 and checks both actual socket endpoints and the OS route after connecting. A changed physical adapter requires fresh discovery. No default route or firewall rule is changed. [Windows route selection](https://learn.microsoft.com/en-us/powershell/module/nettcpip/find-netroute) · [Winsock IPv4 interface pinning](https://learn.microsoft.com/en-us/windows/win32/winsock/ipproto-ip-socket-options)

Path settings persist locally in `%LOCALAPPDATA%\USBLinkLab\transport.json`. The standalone relay credential uses `relay-pairing.dpapi`. Legacy `pairing.dpapi` and outbox records retain their format. Reconfiguring the Pi address does not enroll or identify an iPhone. A corrupted path setting leaves the application disconnected until a valid path is saved. The existing `windows.ps1 probe` command diagnoses the direct Apple connection. Use the application's device search and diagnostics for the Pi path.
