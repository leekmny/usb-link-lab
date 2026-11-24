# Windows handoff

## Shared repository and protocol

- [leekmny/usb-link-lab](https://github.com/leekmny/usb-link-lab) is the shared repository. Windows work uses `platform/windows`. iOS owns `apps/ios/` in its own branch.
- The [canonical contract commit](https://github.com/leekmny/usb-link-lab/commit/78f451cd2a0e992defc4bd27af212f3ab538747f) establishes `ULAB1-20261124-R1`. The UTF-8/LF contract SHA-256 is `535a818b4e76c82bf7c782f9aa9482730b13865e1ddb8c3fb0cba2c946a6596f`.
- Consume [the vector corpus](../protocol/vectors/README.md) from the Windows branch or the integrated shared branch. Swift should independently encode and decode its literal bytes with CryptoKit. The Windows codec also matches the companion's `ulab1.json` byte fixtures. Do not use the public fixture keys at runtime.

## Native Windows path

The app opens the Apple Mobile Device Service IPv4 loopback socket at fixed port 27015. It verifies the server socket owner against the Windows service PID, exchanges bounded usbmux property lists, retains devices explicitly marked USB, and rechecks the chosen device before requesting device port 43123. The socket becomes the ULAB stream after the service accepts the request. There is no externally configured host or separate local listener. The in-process client follows the libusbmuxd 2.1.1 wire reference and avoids distributing Apple DLLs or requiring an additional forwarder process. The running Apple service returned one eligible USB device with the iPhone cabled. The port 43123 probe returned `listener_refused`; the physical application stream remains unverified.

The iPhone needs an unlocked, trusted, development-signed foreground application listening on IPv4 loopback port 43123. For QR enrollment, Windows starts CH with the scanned candidate. For six-digit enrollment, Windows and iPhone first perform EC J-PAKE and derive a 32-byte candidate. See the [pairing extension](../protocol/PAIRING_V1.md) and [iPhone agent prompt](IPHONE_PAIRING_UPDATE_PROMPT.md). Existing saved PSKs still support normal CH reconnect. Apple computer trust and application pairing are separate steps.

## Build and conformance

Windows x64 build 26300 was used for local verification with CPython 3.12.16, PySide6 Essentials 6.10.2, cryptography 50.0.0, PyInstaller 6.19.0 and the locked dependencies. The application uses PySide6 Widgets and native Windows Qt rendering. The first-run theme is light and the first-run language is Korean. English, dark theme and large text are available in Settings.

```powershell
./apps/windows/windows.ps1 setup
./apps/windows/windows.ps1 test
./apps/windows/windows.ps1 probe
./apps/windows/windows.ps1 run
./apps/windows/windows.ps1 package
./apps/windows/windows.ps1 smoke
```

The Windows tests read the shared fixtures without requiring a connected device. To reproduce the companion corpus comparison, fetch `ios/native-client` and pipe `git show origin/ios/native-client:protocol/vectors/ulab1.json` to `apps/windows/.venv/Scripts/python.exe tools/windows_check_ios_vectors.py`. The optional [streaming benchmark](windows-streaming-evidence.md) uses clearly scoped synthetic data and an isolated Python TCP peer. The production launch path has no test peer option.

## Release and hardware gates

The one-directory bundle is an unsigned Windows x64 application. Extract the ZIP and retain the whole `USBLinkLab` folder. The host needs a compatible Microsoft Visual C++ x64 Redistributable and Apple device support for physical iPhone communication. The portable bundle embeds its own Python and Qt runtime. See the [Windows README](../apps/windows/README.md) and [GitHub Windows build status](https://github.com/leekmny/usb-link-lab/actions/workflows/windows.yml?query=branch%3Aplatform%2Fwindows).

The iPhone app was still in development at the Windows handoff. The first physical USB handshake, live Swift interoperability, four-mode file and message exchange in both directions with wireless paths disabled, cable removal, and explicit retry all remain pending. For those tests, attach the unlocked iPhone using a data cable, approve Trust This Computer if prompted, and return USB ownership to Windows if WSL had attached it. Install and launch the signed listener. Record the iOS/Apple support versions and sanitized outcomes in the [verification matrix](verification-matrix.md).

## Local data and threat model

App pairing uses current-user Windows DPAPI. The outbox is bound to the selected USB device and pairing, and each enqueued source is copied to app-controlled storage. Receive completion commits file bytes and metadata before the peer can receive the final ACK. Transport encryption covers the selected wire item. Chat history, temporary snapshots, exports and screenshots are local readable data. Keep them out of public artifacts.

Every mode authenticates framing and control. Plaintext leaves metadata and content unauthenticated, while HMAC-SHA256 gives content authentication with readable payloads. The two AEAD modes also encrypt metadata and content. Item IDs, lengths, timing and mode remain visible. A lost final ACK can leave a committed copy on the receiver, so a manual retry may duplicate it. ULAB v1 uses a pre-shared key and has no forward secrecy. The lab exercises its own application stream and does not require an observer, an account server, or an Internet connection.
