# USBLinkLab enrollment v1

This document defines the two ways to obtain the 32-byte ULAB v1 pairing secret. It does not change the ULAB v1 handshake, per-item protection, fixed USB application port 43123, or canonical contract hash. Only endpoints receive the secret. No network service or wireless data path is used.

The PC is the initiator C and selects an eligible USB device explicitly. The iPhone is responder S. Each endpoint accepts one explicitly armed enrollment attempt. An invitation is valid for 120 seconds and is revoked on cancellation, regeneration, expiry, or use. The deadline authorizes starting the attempt. Once consumed, the regular ULAB handshake uses its own 10-second timeout. A failure requires a new invitation. The human must prepare the iPhone app before starting the Windows connection. Endpoint clocks do not need to agree. Neither endpoint overwrites an existing credential before its new ULAB handshake authenticates the peer.

## Optical QR enrollment

1. C independently generates 32 bytes with the OS CSPRNG. Render an ordinary QR code containing literal ASCII ULABQR1: followed immediately by the canonical base64url representation of those bytes without padding (43 characters). The QR has no URL action. S scans it in the app and strictly checks the prefix, alphabet and decoded length. Reject whitespace, extra suffixes, alternate encodings and the prohibited public fixture secret.
2. C and S hold the candidate in memory only. When C starts enrollment, the first bytes on USB are the ordinary ULAB0001 client hello (CH), authenticated with the candidate PSK. S uses the armed QR candidate for this connection and consumes it even if authentication fails. Never retry this same stream with a previous credential.
3. Save the candidate using current-user DPAPI on Windows and this-device-only Keychain on iPhone only after the usual local handshake completes and before processing application frames. After that, all reconnects use the normal ULAB v1 session. A recorded photograph of the QR contains the key. Avoid screenshots, previews in the app switcher and copies to logs or the clipboard.

## Six-digit enrollment

C displays six uniformly random ASCII digits, including leading zeroes. S takes the exact six characters after user entry. A code is not a ULAB PSK. Both endpoints use the pinned Mbed TLS 3.6.7 EC J-PAKE API with its client/server roles, secp256r1 and SHA-256. This uses Mbed TLS's Thread-derived message profile. It uses the EC J-PAKE primitive, without a TLS connection or TLS wrapping of application data. Use the shared C bridge in native/usb_pairing/ and pass the six literal ASCII bytes exactly once. No PIN stretching, parsing as an integer, trimming, or normalization is applied.

Each enrollment record is 8 magic bytes ULABP001, one stage byte, two big-endian unsigned body length bytes, and exactly that many body bytes. Authenticate and frame the exact bytes over the reliable stream, without relying on receive boundaries. The stage sequence and directions are fixed.

| Stage | Direction | Body |
| --- | --- | --- |
| 1 | C to S | Mbed TLS EC J-PAKE round-one message as client |
| 2 | S to C | Mbed TLS EC J-PAKE round-one message as server |
| 3 | C to S | Mbed TLS EC J-PAKE round-two message as client |
| 4 | S to C | Mbed TLS EC J-PAKE round-two message as server |
| 5 | C to S | 32-byte client confirmation HMAC |
| 6 | S to C | 32-byte server confirmation HMAC |

Validate magic, direction/stage and length before reading any variable body. Stages 1 through 4 have bounds 1 to 512 bytes inclusive; stages 5 and 6 are exactly 32 bytes. Pass opaque share bytes to the bridge. S reads each C share, verifies it with the bridge, then writes the matching S share. C writes its share and verifies the matching S share before advancing. Fresh EC J-PAKE contexts are mandatory per attempt. Never send the code, shared result, PSK, or a password verifier.

Let T be SHA-256 of the four complete encoded stage 1 through 4 records concatenated, including every header. Let Z be exactly the 32-byte output of the shared bridge using mbedtls_ecjpake_derive_secret. Derive each 32-byte result independently using HKDF-SHA256 with IKM Z, salt T and the exact ASCII info below without a trailing zero byte.

| Result | HKDF info |
| --- | --- |
| Candidate PSK | ulab-ecjpake/1/psk |
| K_C | ulab-ecjpake/1/client-confirm |
| K_S | ulab-ecjpake/1/server-confirm |

Stage 5 is HMAC-SHA256 with K_C over ASCII ULABP001/C, one zero byte, then T. Stage 6 uses K_S over ASCII ULABP001/S, one zero byte, then T. S checks stage 5 in constant time before emitting stage 6. C checks stage 6 in constant time before sending ULAB v1 CH with the candidate PSK. There is no magic change or reconnect between stage 6 and the authenticated ULAB0001 handshake. C and S save the candidate after successful local ULAB authentication. No META, OPEN, or application processing occurs before that handshake succeeds.

Use a 20-second total enrollment-record timeout capped by the remaining invitation lifetime. Close on malformed records, key confirmation failure, wrong PIN, timeout, EOF or cancellation. Do not log key material, code, proofs, QR contents or pairing bytes. A short random code bounds an active guess to the one explicitly allowed attempt; it is unsuitable as the persistent PSK.

## iPhone dispatch and reconnect

On an accepted USB-forwarded stream, read the first eight bytes before dispatching, with a five-second limit capped by the remaining invitation lifetime if armed. When a code is armed, ULABP001 consumes it and begins stage 1, with the initial eight bytes accounted for in the parser and transcript. When a QR candidate is armed, ULAB0001 consumes it and enters the regular handshake with the candidate. Without enrollment armed, ULAB0001 uses the saved key. A truncated, unknown, or wrong-method prefix closes the stream without consuming the invitation. Once the expected prefix arrives for the armed method, consume the invitation before reading the remaining bytes. Preserve that prefix for the selected parser as needed. Do not add in-band fallback or negotiate new frame types.

The deterministic fixture contains synthetic inputs for encoding, derivation and confirmation checks. Test live exchanges additionally with fresh native contexts and an actual Windows/iPhone USB trial.

Sources: [Mbed TLS 3.6.7](https://github.com/Mbed-TLS/mbedtls/releases/tag/mbedtls-3.6.7), [EC J-PAKE API](https://mbed-tls.readthedocs.io/projects/api/en/v3.6.7/api/file/ecjpake_8h/), [HKDF RFC 5869](https://www.rfc-editor.org/rfc/rfc5869.html), [base64url RFC 4648](https://www.rfc-editor.org/rfc/rfc4648.html#section-5).
