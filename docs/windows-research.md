# USBLinkLab research purpose

USBLinkLab is a controlled USB security research instrument. The initial experiment uses cooperating Windows and iPhone endpoints running the same ULAB v1 contract and synthetic text, images, and files. Each item selects Plaintext, HMAC-SHA256, AES-256-GCM, or ChaCha20-Poly1305 before transmission. The experiment uses owned or explicitly authorized devices and keeps both application traffic and application pairing out of wireless and Internet paths.

## Evidence to collect

| Question | Windows baseline | Physical acceptance |
| --- | --- | --- |
| What travels in readable form? | Compare literal serialized application records with known fixture bytes. Plaintext and HMAC modes carry readable content. The AEAD modes protect metadata and content while routing fields, lengths and timing stay visible. | Confirm the direct USB byte stream on the actual paired endpoints before drawing any conclusions about intermediate observation. |
| Does each mode protect integrity? | Use fixed malformed or altered fixtures as decoder input. Compare tag failures in the protected modes and expected plaintext behavior. | Repeat end-to-end checks against Swift with synthetic data and verify both endpoints report the same receipt status. |
| How does the link perform? | Measure reproducible staging and final-ACK times, process working set and independent SHA-256 on Python loopback. | Record device and software versions. Use the same content, mode, direction, byte count and timing boundaries for each wired trial. Record errors, cancellation and retry separately. |
| Is the link genuinely USB-only? | Check fixed-port service ownership and USB device selection in tests. | Disable wireless paths for the trial, then unplug the USB cable during a transfer. Verify interruption and a fresh authenticated session during explicit retry. |

## Later Raspberry Pi phase

Direct USB validation is the prerequisite for the later Raspberry Pi 5 and Wireshark research phase. Treat intermediate observability, relay performance, and reliability as hypotheses until measured on the physical setup. For consistent comparison, retain the same test corpus, endpoint builds, protection variants, direction, and timing boundaries as the direct baseline. Sanitize results and use synthetic content in any capture kept for evidence. The Windows release includes the endpoint adapter and synthetic conformance evidence.

See the [Windows handoff](windows-handoff.md), [verification matrix](verification-matrix.md), and [canonical protocol](../protocol/PROTOCOL_V1.md).
