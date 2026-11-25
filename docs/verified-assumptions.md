# Verified assumptions

| Area | Upstream evidence | Local observation |
|---|---|---|
| Physical transport | libusbmuxd documents the Windows Apple multiplexing service and USB-specific device lookup. Upstream declares TCP port 27015 on Windows. | Windows x64 build 26300 was available. Initial inspection found no running Apple support. After attaching the iPhone, PnP reported Apple Mobile Device USB Composite Device, the service was running, and the verified usbmux list contained one USB device. Device port 43123 refused the listener probe. |
| iPhone listener | PeerTalk demonstrates a USB-forwarded app socket. Apple's development signing and Developer Mode are separate from App Store publication. | The iOS prompt is present in the sibling workspace. No signed listener or test device was available at initial inspection. |
| Windows/WSL | Microsoft documents that Windows cannot use a device while it is attached to WSL through usbipd. | No `usbipd` command was detected in the active PowerShell session. |
| Protecting content | Python cryptography documents AES-GCM and ChaCha20-Poly1305 with ciphertext followed by a 16-byte tag. ULAB v1 separately authenticates routing and control. | The canonical specification was verified byte-for-byte against the provided appendix and the shared GitHub repository. |
| User interface and packaging | Qt for Python supplies PySide6 widgets and the Windows platform plugin. PyInstaller produces a platform-specific distribution. | The Windows development environment uses CPython 3.12.16, PySide6 Essentials 6.10.2, cryptography 50.0.0, pytest 9.0.2, and PyInstaller 6.19.0. |
| Raspberry Pi | Raspberry Pi OS gadget networking and Linux usbmon address different parts of a future experiment. The gadget is a USB network adapter. | Pi hardware, capture, and relay remain outside the endpoint acceptance tests. |

## Upstream references

- [libusbmuxd project](https://github.com/libimobiledevice/libusbmuxd) and [Windows constants and messages](https://github.com/libimobiledevice/libusbmuxd/blob/master/include/usbmuxd-proto.h)
- [PeerTalk](https://github.com/rsms/peertalk), [Apple developer account constraints](https://developer.apple.com/help/account/basics/about-your-developer-account), and [Developer Mode](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)
- [WSL USB attachment](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)
- [Python AEAD APIs](https://cryptography.io/en/stable/hazmat/primitives/aead/), [Qt for Python](https://doc.qt.io/qtforpython-6/), and [PyInstaller build platform](https://pyinstaller.org/en/stable/operating-mode.html)
- [Raspberry Pi gadget networking](https://www.raspberrypi.com/news/usb-gadget-mode-in-raspberry-pi-os-ssh-over-usb/) and [Linux usbmon](https://docs.kernel.org/usb/usbmon.html)
