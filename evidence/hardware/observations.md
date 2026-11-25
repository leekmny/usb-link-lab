# Physical USB observations · 25 November 2026

Windows 11 used the production Pi transport and ULAB session code to reach a signed foreground USBLinkLab iPhone app through a Raspberry Pi 5. The Pi’s front TCP listener was bound to the USB Ethernet gadget. Its upstream TCP listener belonged to iproxy on IPv4 loopback, and usbmuxd selected the attached trusted iPhone using USB-only discovery. The observed iPhone product type was `iPhone18,2`, iOS 27.2.1. There was no SSH tunnel for application bytes. This record omits device identifiers, endpoint addresses, keys, raw captures and private storage paths.

## First authenticated session

At **10:01:38.251 UTC**, Windows recorded the final authenticated completion ACK for a 27-byte Plaintext text item. The measured sender-side job-to-final-ACK interval was **95.355 ms**. The Pi’s active-session counters showed 681 forward and 326 reverse bytes at the reported sample. These counters include protocol overhead. End-to-end authentication used the existing saved endpoint credential.

| Pi observation point | Packets | PCAPNG bytes | SHA-256 of the raw private capture |
| --- | ---: | ---: | --- |
| Windows-facing gadget | 17 | 2,860 | `4333f53e032ef67c58b2bedf64a8b489992b130991e2e166cdc84d3c59cdf9e5` |
| Loopback connection to iproxy | 17 | 3,044 | `f94e5a950059e04b593a9032f850959eb5a1505976d4ce04f9a39e44f0c9cfb5` |

The Windows remote helper sent the gadget capture to native Wireshark over pinned OpenSSH, received 2,860 bytes and exited successfully. Reopening the saved file in Windows Wireshark 4.6.10 displayed eight ULAB packets under the `ulab` filter. Gadget frames 4, 6 and 7 contain CH, SH and CF. Frame 14 contains both DATA and END. Frame 16 contains the returning ACK. TShark recovered 27 original bytes from frame 14's DATA body. Their SHA-256 equals the source `6fd1a34aceb7b996fc6d6e4cfd9c1b0876e8c11a1f0d5662f73b0f7011fd2f86`. The Lua view reports observed authentication as unverified. The endpoint's local completion event establishes its validation of the ACK.

The private native GUI screenshot of expanded frame 14 is 1463×914 with SHA-256 `74fd9bd0431cf5ca44df72cd36997042d8d046acfe21961ca9985c81f8298d53`. The initial three-second idle capture before this run contained zero packets and measured 340 bytes.

## Endpoint transfers

One outgoing session per mode sent a 27-byte marker, an 18,569-byte PNG and a 66,304-byte binary file. All **12 Windows-to-iPhone jobs** received an authenticated complete ACK. The times below are milliseconds measured by the originating Windows endpoint on a monotonic clock. There is one sample per condition. These measurements do not isolate cipher time or one-way latency.

| Windows → iPhone mode | Text completion | PNG completion | Binary completion | Marker in captured outgoing DATA |
| --- | ---: | ---: | ---: | --- |
| 0 · Plaintext | 58.971 | 15.697 | 17.964 | Visible |
| 1 · AES-256-GCM | 88.619 | 18.465 | 22.964 | Absent |
| 2 · ChaCha20-Poly1305 | 90.820 | 21.003 | 16.685 | Absent |
| 3 · HMAC-SHA256 | 94.311 | 19.491 | 17.426 | Visible |

At **10:04:42.752 UTC**, the Windows receiver committed a 14-byte AES-256-GCM text sent by the iPhone. Its independently computed saved-file hash matched the synthetic return fixture, SHA-256 `35178dee58c241b43e2b29600d050378643460834f6044e23bd6cebe6bb93e29`. The reverse DATA appears on both observation points in the session whose outgoing items used Plaintext.

In a separate transfer, Windows received a user-selected 531,989-byte AES-256-GCM image from the iPhone, validated the authenticated END digest, saved the image, and rehashed the saved file successfully. An independent copy of the sender’s original was unavailable for comparison. Coverage reached **14 of 24** distinct mode × payload type × direction combinations: all 12 Windows-originated cases and two iPhone-originated AES returns (text and image). The earlier Plaintext proof repeated a covered case.

The independent Windows extractor reproduced the generated 18,569-byte PNG from both physical tap points in both clear modes. The reconstructed bytes matched the source SHA-256 `9c6a30e4f07160d26bfb0e65a065865bfc7f6058772c7183c015247db4701079`. It exported no content from the AES-GCM or ChaCha capture files. The binary source hash was `fdd315efecb12292b99de9182a440815a818bf6dcba7704012f26589327cb045`. To complete the independent endpoint comparison for the generated PNG, export the copy saved by the iPhone and hash those bytes. The separate encrypted return image establishes reverse image delivery and a Windows disk readback.

## Packet evidence for the four sessions

Each capture file spans all three outgoing items in its mode, including TCP, the handshake and control traffic. The Plaintext session also includes the encrypted return text. Counts and sizes therefore describe a whole capture. The capture records reported zero packet drops for these eight files. A Windows TShark PDML pass checked the individual ULAB DATA records per direction despite coalescing. The outgoing application DATA bodies had original lengths 27, 18569, 65536 and 768 bytes on both legs in each case. Mode 0 and mode 3 exposed the known marker. Modes 1 and 2 yielded ciphertext DATA without that marker. Those observations are complemented by authenticated endpoint completion and offline negative-tag tests.

| Outgoing mode | Pi layer | Packets | PCAPNG bytes | SHA-256 |
| --- | --- | ---: | ---: | --- |
| Plaintext | Gadget | 127 | 100,444 | `67a39db11ec01b7a60d7e31feb38da71e2c652a601b137b533aeb3698b779b91` |
| Plaintext | Loopback | 118 | 101,048 | `8a15ad6753b0631ec39f012e424d48aafa832815547d1ec423705e2b3e6c9e22` |
| AES-256-GCM | Gadget | 122 | 99,308 | `0935e1250b5d991c73c999d4f41165b7ce6ebc27c354754dedbdfbe32a497eea` |
| AES-256-GCM | Loopback | 111 | 99,652 | `cb458a65c22c7dd39b37ee932fde238fec65bce0df2801d14ab8394131756451` |
| ChaCha20-Poly1305 | Gadget | 124 | 99,484 | `920b87cbcee990f4f53f500f86a5f1df31c2f7583e2a18ccac12787fd278cad8` |
| ChaCha20-Poly1305 | Loopback | 117 | 100,252 | `ee40a1acfc3bf54dbfb29a006fb33d5230eda1549cec0e020cae1a97e478ae3e` |
| HMAC-SHA256 | Gadget | 122 | 98,964 | `a83e1788277d8607bebcb71bb4a4c9aa9c14a21645362294c7871f51ecac8d46` |
| HMAC-SHA256 | Loopback | 117 | 99,908 | `797a55e359e489433e0421b9a00ecdfcf8e428fd89daa4442afed40e042be54f` |

The [Windows hardware runner](../../tools/hardware_lab.py) used the production adapter, handshake and job storage on Windows. Its recorded SHA-256 was `ce0f1e48479321f20023565ff8eb30743e32bf2935127ce19df68f504ca45c7d`. The recorded adapter and session SHA-256 were `735eaac89eaefbab79b907486a689913a0dd4cc8d4d944e6d20155dfd5b8ff76` and `84894745365424656cffb79440bf700965b87aac85cdb47a7d5b52fc76e3e959`. For capture decoding use the [dissector and extraction guide](../../docs/ulab-dissector.md). The [acceptance matrix](../../docs/acceptance.md) tracks the remaining independent physical controls.
