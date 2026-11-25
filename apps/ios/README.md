# USBLinkLab

USBLinkLab is a paired Windows and iPhone application for evaluating message and file protection over a physical USB link. The desktop opens a USB-selected usbmux stream to the iPhone's foreground IPv4 loopback listener. Both endpoints can then send items on the same connection.

| Protection | Confidentiality | Content authentication |
| --- | --- | --- |
| Plaintext | No | No |
| AES-256-GCM | Yes | Yes |
| ChaCha20-Poly1305 | Yes | Yes |
| HMAC-SHA256 | No | Yes |

The mode is frozen at enqueue. A complete delivery requires the receiver's durable save followed by its authenticated acknowledgment. The experimental protocol has no forward secrecy. Saved files, exports and screenshots have separate protection properties.

Open a completed sent or received text message in History and choose Delete message to remove its local copy. To remove several, choose Select in History, mark the messages, choose Delete selected messages, and confirm the count.

## Reconnect over USB

With the iPhone app open in the foreground and the device unlocked, Windows 0.2.3 or later retries the same paired iPhone after an unexpected USB disconnect. The iPhone accepts a fresh authenticated session. Resume the iPhone queue if items were waiting when the connection dropped. Interrupted or unconfirmed items require an explicit retry.

## Project

- [ULAB v1 shared protocol](../../protocol/PROTOCOL_V1.md)
- [Public interoperability fixtures](../../protocol/vectors/ios-corpus.md)
- [QR and six-digit USB pairing](../../docs/ios-pairing.md)
- [iPhone build, signing and Windows handoff](../../docs/ios-handoff.md)
- [Verification status](../../docs/ios-verification-matrix.md)
- [Documentation findings and device observations](../../docs/ios-verified-assumptions.md)

The canonical contract is revision `ULAB1-20261124-R1`, established at commit `78f451cd2a0e992defc4bd27af212f3ab538747f`. Its SHA-256 is `535a818b4e76c82bf7c782f9aa9482730b13865e1ddb8c3fb0cba2c946a6596f`. Coordinate shared changes with both platform owners. The iPhone implementation lives under `apps/ios/` and Windows development under `apps/windows/`.
