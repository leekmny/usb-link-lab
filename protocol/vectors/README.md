# ULAB v1 interoperability vectors

These UTF-8 JSON files use the test-only PSK and nonces from the specification. Each scenario is an independent session with both sequence counters starting at zero. The fixtures were generated with Python 3.12.16 and cryptography 50.0.0 using the protocol's byte layout and domains independently of the application encoder. The published A6 handshake and DATA record values were checked during generation. [Appendix A6](../PROTOCOL_V1.md#a6-deterministic-interoperability-fixtures)

## Positive records

| Field | Contents |
|---|---|
| `handshake_hex` | The PSK, two 32-byte nonces, three full handshake records and the SHA-256 transcript hash. Values are lowercase hexadecimal. |
| `keys_hex` | All five HKDF outputs for each direction, as lowercase hexadecimal. |
| `scenarios[].records` | Ordered application records, including interleaving across directions. |
| `direction` | `c2s` for client-to-server or `s2c` for server-to-client. Select keys using the frame sender. |
| `type`, `mode` | Protocol enum names. |
| `sequence`, `index`, `item_id_hex` | The sender-direction sequence, chunk index and raw 16-byte item ID. |
| `body_hex` | The exact original body, including any JSON whitespace and escape sequences. |
| `wire_hex` | The complete record, including the four-byte length prefix. |

Use `body_hex` as bytes when testing the encoder and compare the result with `wire_hex`. Feed `wire_hex` to the decoder and compare all recovered fields and body bytes. The four mode scenarios include Korean text and a generated one-pixel PNG in opposite directions. The boundary scenario covers 0, 1, 65,535, 65,536 and 65,537-byte files, embedded zero bytes and a Korean UTF-8 character split across DATA frames. Another scenario interleaves modes, active items and drain messages.

## Rejected inputs

Each `handshakes` entry specifies the local `role`, test `psk_hex` and concatenated inbound `received_hex`. Supply the local role's fixed nonce and require handshake rejection.

Each `frames` entry supplies the complete input bytes available to the decoder, sender-direction key label and receiver's next expected sequence. End the input stream at the last supplied byte for truncation cases. The `error` is `authentication` for a failed cryptographic check or `protocol` for invalid framing, authenticated fields or body schemas.

The fixture tests can be run from the repository root.

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q apps/windows/tests/test_protocol.py
~~~
