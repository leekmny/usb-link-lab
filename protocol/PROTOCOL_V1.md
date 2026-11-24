## Appendix A. Shared protocol: ULAB version 1

Use this appendix unchanged in both implementations. It is a deliberately limited, custom educational protocol, not a reviewed production security protocol. It has no forward secrecy. Compromise of the pairing secret allows recovery of recorded sessions. Use the platform cryptographic libraries; do not implement the primitives.

### A1. Scope and mandatory modes

The transport supplies one reliable, ordered, full-duplex byte stream. Windows is client C; iPhone is server S. The stream may be forwarded by usbmuxd. Transport callbacks are not message boundaries. Implement exact-length reads and complete writes with bounded buffering.

| Mode byte | Name | META, DATA and END body protection |
|---|---|---|
| 0 | PLAINTEXT | Original bytes; no content authentication |
| 1 | AES256GCM | AES-256-GCM, 12-byte nonce, 16-byte tag |
| 2 | CHACHA20POLY1305 | IETF ChaCha20-Poly1305, 12-byte nonce, 16-byte tag |
| 3 | HMACSHA256 | Original bytes followed by a 32-byte HMAC |

All four modes are mandatory. Mode 3 provides integrity/authenticity without confidentiality. Every frame header and the OPEN/ACK/ERROR/DRAIN control bodies are authenticated independently of the selected mode.

Do not wrap this experiment stream in TLS: modes 0 and 3 must expose actual content bytes at this application stream boundary. TLS 1.3, if added later, is a separate connection option whose encryption changes wire observability, never another item mode.

Snapshot the chosen mode when the user enqueues each outgoing text, image or file. Store an immutable job with that mode and stable source bytes; later dropdown changes affect only subsequently enqueued jobs. Allow at most 32 outstanding local jobs, counting the active job and pending queue; reject another enqueue visibly instead of dropping an existing job. Check storage before staging files and keep file contents out of the UI queue's memory. Display the actual mode on every item. The receiver uses the authenticated incoming mode, not its own current dropdown.

### A2. Bytes, secrets and handshake

All integers below are unsigned, big-endian. Concatenation is written ||. ASCII domain strings use exactly the shown bytes; \0 means one zero byte. HMAC means HMAC-SHA256 with the complete 32-byte output. Compare/verify tags using vetted library verification functions.

Provision one independently generated, random 32-byte pairing secret PSK through an out-of-band channel, such as locally displaying/scanning a QR code or manually copying 64 hexadecimal digits. Never use a short PIN, password, UDID or device name as PSK. Never send PSK through this stream. Store it with Windows credential protection and iOS Keychain. A single configured peer is sufficient for version 1.

On every connection C generates fresh random Nc[32]; S independently generates fresh random Ns[32]. Fixed magic M is ASCII "ULAB0001" (8 bytes). Handshake records have the following fixed lengths and no application frame prefix:

~~~
C0 = M || 0x01 || Nc
CH = C0 || HMAC(PSK, "ULAB1-CH\0" || C0)                  # 73 bytes
S0 = M || 0x02 || Ns
SH = S0 || HMAC(PSK, "ULAB1-SH\0" || CH || S0)            # 73 bytes
F0 = M || 0x03
CF = F0 || HMAC(PSK, "ULAB1-CF\0" || CH || SH || F0)      # 41 bytes
TH = SHA256(CH || SH || CF)
~~~

C sends CH. S checks its magic, type and HMAC before sending SH. C checks SH against its exact CH before sending CF. S verifies CF before accepting application frames. C can send application frames immediately after CF. Reject incorrect magic, versions, types or authentication; use a 10-second handshake timeout and close on failure. There is no negotiation, fallback, unauthenticated pairing, or application processing before this handshake completes locally.

Derive ten independent 32-byte keys:

~~~
K(d,p) = HKDF-SHA256(
    IKM = PSK, salt = TH,
    info = ASCII("ULAB1/" + d + "/" + p), length = 32)
d = "c2s" or "s2c"
p = "hdr", "ctl", "aes", "chacha", or "hmac"
~~~

The info strings contain no terminal zero byte. Use the appropriate sender-direction keys for every frame, including ACKs. Fresh nonces/transcript produce fresh keys on every reconnect; do not cache session keys or reuse handshake nonces.

### A3. Application frame

~~~
U32(L) || H[40] || header_mac[32] || wire_body
~~~

L counts every byte after U32(L), including H, header_mac and wire_body; it excludes its own four bytes. Header H has exactly this layout:

| Offset in H | Size | Field |
|---|---:|---|
| 0 | 1 | Version, always 1 |
| 1 | 1 | Type: OPEN=1, META=2, DATA=3, END=4, ACK=5, ERROR=6, DRAIN=7 |
| 2 | 1 | Item mode, 0 through 3 |
| 3 | 1 | Flags, always 0 |
| 4 | 8 | Sender-direction session sequence number |
| 12 | 16 | Raw item ID bytes |
| 28 | 8 | Chunk index |
| 36 | 4 | n: length of the original body before protection |

Python's exact packing format is >BBBBQ16sQI. Do not serialize a language-native struct, UUID object, or native-endian integer.

~~~
prefix = U32(L)
header_mac = HMAC(K(d,"hdr"), "ULAB1-HDR\0" || prefix || H)
AAD = "ULAB1-PAYLOAD\0" || prefix || H
nonce = 0x00000000 || U64(sequence)       # exactly 12 bytes
~~~

Read only prefix, fixed H and header_mac first. Reject L outside [72, 1048576] before any variable allocation. Verify header_mac before trusting header fields or reading a variable body. Then require the exact length equation L = 72 + n + overhead:

| Body category | wire_body | Overhead |
|---|---|---:|
| OPEN, ACK, ERROR, DRAIN | concat(P, HMAC(K(d,"ctl"), concat("ULAB1-CTL\0", prefix, H, P))) | 32 |
| META, DATA, END; mode 0 | P | 0 |
| META, DATA, END; mode 1 | AESGCM(K(d,"aes"), nonce, P, AAD), ciphertext then tag | 16 |
| META, DATA, END; mode 2 | ChaCha20Poly1305(K(d,"chacha"), nonce, P, AAD), ciphertext then tag | 16 |
| META, DATA, END; mode 3 | concat(P, HMAC(K(d,"hmac"), concat(AAD, P))) | 32 |

P is exactly n bytes. Do not transmit a second nonce: derive it from the authenticated sequence. CryptoKit encoders must concatenate sealedBox.ciphertext and sealedBox.tag, not sealedBox.combined, which also carries a nonce. Construct the received CryptoKit sealed box with the derived nonce and separate ciphertext/tag. Python AEAD encrypt/decrypt uses ciphertext followed by the 16-byte tag.

Maintain one monotonically increasing sequence counter per sender direction, starting at zero after a fresh handshake, across all item modes and all frame types. Each receiver requires exactly its next expected value. One writer actor/queue must reserve sequences, encode and write frames serially. Never interleave frame bytes from concurrent tasks. Reject duplicates and gaps. Never reset the counter at an item boundary or on a mode change.

### A4. Body schemas and transfer state

All bodies except DATA are flat UTF-8 JSON objects without BOM. The schemas below are exact: decoders reject missing/extra fields, wrong types, malformed UTF-8/JSON, invalid Unicode scalar strings, and values outside bounds. Reject duplicate member names after JSON unescaping, including "mode" repeated as "\u006dode"; do not accept a parser's silent first/last-value choice. Nested objects, arrays and null are invalid. Numeric fields require the JSON token grammar 0|[1-9][0-9]* and checked unsigned conversion: reject booleans, signs, fractions and exponent notation, even 1.0 or 1e0. Do not coerce strings to numbers. Python must not treat bool as int; Swift must not allow NSNumber/decoder coercions to bypass these checks. Use a duplicate-aware parser or a bounded lexical prevalidation pass before schema decoding. JSON field order and insignificant whitespace need not match: authenticate the exact received byte buffer before parsing; never reserialize JSON for MAC or AEAD verification. Deterministic tests use literal fixture JSON bytes.

Each sender has at most one active outgoing item; both directions may operate simultaneously. Each new item ID is a cryptographically random nonzero 16-byte value, never reused by that sender in this session. Allow at most 4096 originated IDs per direction per session, including accepted OPENs that have not completed. Track bounded seen-ID sets and state by originating direction; an ACK references an item originated by the opposite direction. Retain seen IDs until session closure and clear them only after abandoning that session. Local queue entries receive a fresh ID when admitted to a session.

1. Enter WAIT_READY, then send OPEN with chunk index 0 and P = {"mode":m}. Body mode must equal header mode. After validating both header and control MACs, the receiver freezes this item ID and mode. Unsupported modes or conflicting state fail; there is no automatic downgrade.
2. Receiver sends ACK with the same item ID/mode, index 0, and P = {"stage":"ready"}. Sender waits for this authenticated acceptance before META.
3. Sender sends exactly one META, index 0. Its chosen-mode-protected body is {"kind":"text","name":"","media_type":"text/plain; charset=utf-8","size":13,"chunk_size":65536}, with actual values. kind, name and media_type are strings; size and chunk_size are unsigned JSON integers. kind is "text", "image" or "file". For text, name is empty, media_type is exactly "text/plain; charset=utf-8", and size is at most 1048576 bytes. For image/file, name is a nonempty display filename of at most 255 UTF-8 bytes, size is at most 1073741824 bytes, and media_type is 1–127 printable ASCII bytes; image media types start with "image/". A name is never a destination path. size counts exact content bytes; chunk_size must be 65536. Text is UTF-8 bytes, images/files are original selected file bytes with an accurate media type, using "application/octet-stream" for an unknown file type. Never silently transform an image after declaring its size.
4. Send DATA with indices 0, 1, ... and raw content chunks. Every nonfinal chunk contains 65536 bytes; the final chunk contains the exact remaining 1–65536 bytes. No zero-length DATA is allowed. Mode and ID must match the frozen OPEN throughout.
5. Enter WAIT_COMPLETE, then send END with header chunk index C and body {"bytes":N,"chunks":C,"sha256":"lowercase_hex_digest"}. N and C are unsigned JSON integers; the digest is a string of exactly 64 lowercase hexadecimal characters encoding SHA256 of the concatenated original content bytes only. Require N = META.size, C = ceil(N/65536), all expected indices, actual received count N, and matching digest.
6. Only after these checks and successful local commit does the receiver send ACK {"stage":"complete"}, same ID/mode, index 0. Local commit means completing file flush/synchronization and close through public platform APIs, a same-volume atomic rename into app-controlled storage, and successful persistence of the item's metadata record. The record must reference an existing complete file; do not ACK data held only in RAM or a pending write. This acknowledges the application-local save, not user export or a guarantee against every hardware/power failure. It contains no content digest. Sender marks completion only after this valid ACK. Missing final ACK means completion is unconfirmed; do not automatically resend. If ACK delivery fails after commit, retain the committed receiver item. Manual retry may create a second saved item; version 1 does not provide exactly-once delivery. Accept each ACK only in its corresponding waiting state; reject repeated or out-of-order stages. Bind all asynchronous callbacks to a local session-generation token so a closed session's late ACK/task cannot update a new session.

The zero-byte case is META.size=0, zero DATA frames, END index=0, bytes=0, chunks=0 and the standard SHA256 of empty bytes. It must work for an empty file or empty text if the UI permits the latter.

OPEN/META/ACK/ERROR/DRAIN indices are 0. END's index must equal its chunks field. An ERROR has item ID all zero, mode 0, index 0, and {"code":"protocol_error"}; a valid ERROR closes the session. For authentication/framing failure, close directly without detailed responses.

### A5. Limits, integrity meaning and failure behavior

DATA n is 1–65536. Every JSON P is at most 4096 bytes. Apply the per-kind sizes above before allocating/staging content. Reject unsafe filenames, path separators/traversal, NUL and control characters; save into an app-controlled directory with collision-safe names. Do not execute received files or trust MIME declarations.

Permit sequences 0 through 2^32-1 only, and at most 4294967296 cumulative original body bytes per sending direction per session. Reserve budget before every new outgoing OPEN so finishing an item cannot force rekeying mid-transfer. Let B/F be original body bytes/frame count already committed to the outgoing writer, including unsent queued frames, N the new job's known content length, and R = 4096 minus the number of peer-originated items whose complete ACK has been fully written. Require:

~~~
B + N + 3*4096 + 2*R*4096 + 3*4096 <= 4294967296
F + ceil(N/65536) + 3 + 2*R + 3 <= 2^32
~~~

The terms reserve OPEN/META/END, up to two maximum-size ACKs for every remaining peer item, and two DRAIN messages plus one ERROR. Count/commit queued writes within the single writer so concurrent control frames cannot evade reservations. If admission fails or this sender has used all 4096 IDs, stop admitting new OPENs and drain. Receivers also enforce the hard count/byte/frame limits. Reject integer overflow and invalid lengths. Stream to temporary files with bounded buffers and incremental SHA256; decode text after reassembly or with a proper incremental UTF-8 decoder.

For coordinated drain, DRAIN uses zero item ID, mode 0, index 0 and the fixed control HMAC. Its exact JSON is {"stage":"request"} or {"stage":"ready"}. An endpoint requests drain by stopping new outgoing OPEN admission and writing request after any already-admitted OPEN. On receiving request, stop admitting new local OPENs and send request once if not already sent. Accept earlier in-flight OPENs that arrive before the peer's request; reject any OPEN arriving after that request. Existing items continue through their complete ACKs. Once both requests have been sent/received, the outgoing item has completed, the incoming item has committed and its complete ACK has been fully written, send ready once. If a direction has no active item, that direction satisfies its completion condition. Reject repeated or out-of-order DRAIN stages. C closes only after its ready is fully written and S's ready is received, then performs a fresh handshake before releasing the pending queue. S waits for that close/new connection. This barrier prevents rekeying while an opposite-direction OPEN is in flight. Preserve unsent jobs and their mode snapshots; reset keys/counters/ID sets only with the fresh session.

Mode 0 still authenticates routing, mode selection and control messages, but does not authenticate META/DATA/END content. Its plain SHA256 detects accidental errors only: an active attacker can alter content and replace the visible hash. Do not describe a matching hash or authenticated ACK as proof that plaintext content is authentic. Even after a completed hash check, a mode-0 preview must remain explicitly labelled "Unauthenticated content"; completion never upgrades it to trusted content. Modes 1, 2 and 3 authenticate metadata, chunks and END; modes 1 and 2 also encrypt metadata and content. Item IDs, mode, lengths and timing remain visible.

Do not publish, preview as trusted, or rename staged incoming content as complete until END validates. On disconnect, malformed frame, tag failure or timeout, interrupt active transfers, discard incomplete temporary files and close the session; retain previously committed items. Cancellation is explicitly session-wide: it interrupts active sends and receives in both directions and closes the stream. The UI must state this scope, preserve unsent queued jobs, and keep them paused until the user resumes/reconnects. A sender cancelled after END may have an unconfirmed remote commit.

Version 1 has no resume, reconnect retransmission, or sequence recovery; a retry is a new item in a new authenticated session. Never call a truncated transfer successful because earlier chunks verified. Give WAIT_READY and WAIT_COMPLETE a 60-second deadline from completion of their OPEN/END write; unrelated opposite-direction traffic must not extend them. Other active stages fail after 60 seconds without progress for that item. Begin a 10-second exact-frame-read timeout at its first received byte, not while an otherwise idle stream awaits a frame. Once local items are drained, allow 60 seconds for the remaining DRAIN barrier. Idle sessions without active transfers or drain may remain open. Do not relabel an unconfirmed transfer as complete because a late callback arrives after session closure.

On iOS, foreground-only operation is the baseline. Backgrounding/suspension must lead to a visible interrupted transfer and safe reconnect; do not promise persistent background socket service. At most use supported finite background completion time for cleanup. Distinguish TCP simulation, simulator results and real paired USB-device results in reports.

### A6. Deterministic interoperability fixtures

The following values were calculated and checked using Python 3.12 / cryptography 50.0.0. They have not been executed in Swift/CryptoKit. Both projects must independently assert them; runtime randomness must never use these constants.

~~~
PSK = bytes 00 through 1f
Nc  = bytes 20 through 3f
Ns  = bytes 40 through 5f
CH final HMAC = 8a5a63503ca163aa238da8e13d2d5662ec7fbd03df6c1eeb10317c83452efee0
SH final HMAC = 382c3a8f8c92fc2e5a9c2e9db914f59bc4dbf6e8102e091179fe3b821f978b75
CF final HMAC = 7156cf54bef493f11331f2119980a5d0901c365f38d53bf24162c21c304fd357
TH = a09adab89f2ad1fce26eb1f21e23e8a1094ddfeb9c4c05cdf83b45ed2752cb53
K(c2s,hdr) = f23507808fa3b0ce6e3106d9797939e3bbedca8196249eef2df2249f54ecfd8d
K(c2s,aes) = 436418f1f2ee0db243a50b6c7312b5e68db32a59d0331bce7e014f525b7a1972
K(c2s,chacha) = 95b7791c4eb4e6d88d15617cce92fac26bfc0bcdfd4fae8de0d2a11a530f3b57
K(c2s,hmac) = 207fb0300784e7d80fcafb8685152102b80d448bbf4e4eeb328bd586b4156459
P = ec9588eb85952c20555342210a       # UTF-8 of Unicode "\uC548\uB155, USB!\n", 13 bytes
item_id = 000102030405060708090a0b0c0d0e0f
type = DATA (3), sequence = 2, chunk_index = 0, flags = 0
nonce = 000000000000000000000002
~~~

These are independent record-encoding fixtures, not permission to send the same sequence twice in a live session.

~~~
AES mode 1:
prefix = 00000065
H = 010301000000000000000002000102030405060708090a0b0c0d0e0f00000000000000000000000d
header_mac = 69b21eeb63dee7bf2b1ea369dba6aab155a750941a5031afc0d4691ac550c4eb
wire_body = bb849151471526ecff5220478dd9a5931597bfac524fc3b554dfcb4ee1

ChaCha mode 2:
prefix = 00000065
H = 010302000000000000000002000102030405060708090a0b0c0d0e0f00000000000000000000000d
header_mac = e4bc61e2c4081ee4b6008bd052d7662e0778e54ba81cb932dc7bf9a76ce7bb8e
wire_body = 53695f5666e7845130fd75d8a289094e2f8a60ef5290d3e3bef04ece72

Plaintext mode 0: prefix=00000055, wire_body equals P exactly.
HMAC mode 3: prefix=00000075; build H as above with mode byte 03.
wire_body = ec9588eb85952c20555342210af5eaa80d078481d50b54ee0b9a4a76cd2edf79daeb00b78508fe01bbb07bf342
~~~

Also share a machine-readable fixture corpus with literal JSON hex, complete OPEN/META/DATA/END/ACK records and both directions. Test both encoders against the same expectations and both decoders against each other's records. Include empty/1/65535/65536/65537-byte items, Korean text split across a chunk boundary, binary zero bytes, simultaneous transfers, mixed-mode queues, and mode changes while earlier items wait.

Negative tests must cover wrong PSK, modified handshake nonce/tag, wrong version/mode/flags/length, header manipulation, replay/gap, wrong item ID/index, body/tag corruption, missing END, wrong hash/count, duplicate META/END, duplicate/escaped JSON keys, numeric booleans/fractions/exponents, invalid Unicode, oversized lengths, and interruption/reconnect. Also test item 4096 versus 4097, queue entry 32 versus 33, preflight budget refusal, simultaneous drain requests, OPEN in flight before drain, file-write/metadata-commit failure, lost/late complete ACK, old-session callbacks, and session-wide cancellation while both directions are active. In mode 0, demonstrate both unchanged-hash corruption detection and a same-length content-plus-END-hash modification that succeeds; in modes 1/2/3 that modification must fail authentication. Report real Python↔Swift and physical USB tests only after running them.

### Protocol primary references

- [Python cryptography AEAD APIs](https://cryptography.io/en/latest/hazmat/primitives/aead/)
- [Apple AES.GCM](https://developer.apple.com/documentation/cryptokit/aes/gcm)
- [Apple AES.GCM.SealedBox](https://developer.apple.com/documentation/cryptokit/aes/gcm/sealedbox)
- [Apple ChaChaPoly](https://developer.apple.com/documentation/cryptokit/chachapoly)
- [Apple HKDF](https://developer.apple.com/documentation/cryptokit/hkdf)
- [RFC 5869: HKDF](https://www.rfc-editor.org/rfc/rfc5869.html)
- [RFC 8439: ChaCha20-Poly1305](https://www.rfc-editor.org/rfc/rfc8439.html)
- [Apple background execution guidance](https://developer.apple.com/documentation/uikit/preparing-your-ui-to-run-in-the-background)
