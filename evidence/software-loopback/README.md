# Linux loopback capture evidence

These PCAPNG files came from actual dumpcap capture on an isolated WSL2 Linux loopback interface on 2026-11-25. Both endpoints ran the project's Python Session implementation and exchanged synthetic text and a generated PNG through a separate relay process. Each case has one TCP connection on the client-facing leg and a second TCP connection on the upstream leg. There are 402 packets in eight captures, totaling 371,776 bytes. Physical Pi/iPhone USB operation requires its own evidence.

| Mode | Text packets | Image packets | DATA visibility | Extracted copies per visible capture |
| --- | ---: | ---: | --- | ---: |
| Plaintext (0) | 49 | 52 | Source bytes visible | 4 |
| AES-256-GCM (1) | 47 | 53 | Ciphertext | 0 |
| ChaCha20-Poly1305 (2) | 47 | 54 | Ciphertext | 0 |
| HMAC-SHA256 (3) | 48 | 52 | Source bytes visible | 4 |

Four copies correspond to two sending directions observed on two legs. The receiver roles independently saved and hashed the source in every case. The `extraction.jsonl` files record filenames from the original run. Recreate the extracted files locally with the tool. Each capture's `relay-status.json` gives its actual port numbers. [Reproduction and Wireshark commands](../../docs/software-experiment.md)

The [comparison report](comparison.json) includes the source hashes of the implementations used in the run, UTC times, measured latencies, capture filters and capture SHA-256 values. The [Windows replay report](windows-replay.json) records the independent Windows TShark check for every capture. All captured packets used IPv4 loopback and the two lab listener ports. The capture blocks consist of pcapng section and interface metadata, enhanced packets, and interface statistics. No endpoint private key, QR invitation, six-digit enrollment, hardware identifier or third-party application traffic was part of the test.
