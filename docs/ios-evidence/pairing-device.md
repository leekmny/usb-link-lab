# iPhone device enrollment and USB observation

Observed on 2026-11-24 with a cabled iPhone 17 Pro Max running iOS 27.0.1,
Windows 11 x64, Apple Mobile Device Support 20.0.1.47, and USBLinkLab 0.2.0 for Windows.
The owner signed and upgraded the iPhone with Sideloadly 0.72 and confirmed that the
QR and six-digit menu opened. The installed iPhone candidate was built at
`af7c34cd6f37252d9726afb69b2e4b9c83f5a0cd`, with IPA SHA-256
`6e168dd6e6fa9eb562cb4d988d3179776d64257d540a20010a46cc65e39362e2`.
The owner followed the wireless-off checklist before preparing the invitation.

| Check | Observation | Status |
| --- | --- | --- |
| QR enrollment | Owner confirmed paired state after scanning the Windows invitation. Windows stored a credential protected by DPAPI and exposed an active USBLinkLab connection to the running Apple multiplexing service. | PASS |
| iPhone to Windows text | Two committed AES-256-GCM entries in Windows receive storage, with 6 and 3 content bytes. | PASS |
| Windows to iPhone text | Windows persisted a 15-byte Plaintext and a 19-byte HMAC-SHA256 outgoing item as complete after the final receiver ACK. The owner confirmed the transfers were visible. | PASS |
| Cable removal | The established app-to-Apple-service connection and eligible USB device disappeared at the unplug checkpoint. | PASS |
| Saved credential reconnect | The desktop state was sampled during cable removal; subsequent cabled authentication was not observed on this phone. | NOT RUN |
| Six-digit enrollment | Cabled user check pending. The macOS fixture and native test results are recorded separately. | NOT RUN |

Only direction, mode, byte count and completion state are shown here. The device
identifiers, input values, payload contents and pairing material stayed on the
endpoints. These observations establish the QR path and text exchange on this
setup. Four-mode text/image/file coverage for the device and performance
measurements have separate acceptance criteria.

[iPhone candidate CI](https://github.com/leekmny/usb-link-lab/actions/runs/45341162657)
· [Windows release](https://github.com/leekmny/usb-link-lab/releases/tag/windows-v0.2.0)
