# Inspecting ULAB captures

The Lua plugin reads the Windows-facing gadget TCP stream and the loopback TCP stream to iproxy. Start capture **before enrollment or reconnect**, then send synthetic items in the foreground. Each TCP conversation needs the ULAB0001 client hello, server hello and client finish. The six ULABP001 records can precede the handshake on the same connection. A raw usbmon transfer needs separate USB/usbmux decoding before these TCP instructions apply.

## Load the plugin

From the repository root, open the capture under your normal user account. This loads the repository copy for one process.

~~~powershell
wireshark -X lua_script:dissectors/ulab.lua -r experiment.pcapng
tshark -n -2 -X lua_script:dissectors/ulab.lua -r experiment.pcapng -Y ulab -T fields -e frame.number -e tcp.stream -e ulab.direction -e ulab.record -e ulab.mode
~~~

For a persistent installation, run `tshark -G folders`, copy `dissectors/ulab.lua` into the displayed **Personal Lua Plugins** directory and restart Wireshark. Keep one installed version. An explicitly loaded duplicate reuses the installed dissector. Replace the installed copy when updating. **Analyze → Enabled Protocols** should list ULAB. TCP subdissector reassembly must be enabled. The tested Windows version is Wireshark/TShark 4.6.10. For legacy TShark, use the shown display filter when running two-pass dissection.

The default server ports are gadget 43123 and iproxy 43124. For a changed gadget port, pass `-o ulab.port:45000`. For a changed loopback port, use `-o ulab.iproxy_port:45001`. The direction is inferred from the server port. Select a capture from one observation point per experiment to distinguish the two TCP conversations.

## Display filters

| Display filter | What to inspect |
| --- | --- |
| `ulab` | Enrollment, handshake, item or malformed record |
| `ulab.pair_stage == 6` | Sixth six-digit enrollment record |
| `ulab.handshake_type == 3` | Client finish |
| `ulab.mode == 0` | Plaintext item frames |
| `ulab.mode == 1` | AES-256-GCM item frames |
| `ulab.mode == 2` | ChaCha20-Poly1305 item frames |
| `ulab.mode == 3` | HMAC-SHA256 item frames |
| `ulab.frame_type == 3 && ulab.plaintext` | Visible DATA in modes 0 and 3 |
| `ulab.ciphertext && ulab.aead_tag` | Encrypted META, DATA or END |
| `ulab.invalid` | Missing captured handshake or invalid record boundary |
| `ulab.direction == "c2s"` | Windows-to-iPhone records |

Display filters select packets. A packet can contain several ULAB records, including records from different items or modes. Expand their individual subtrees. `ulab.version`, `ulab.flags`, `ulab.frame_len`, `ulab.sequence`, `ulab.item_id`, `ulab.chunk_index` and `ulab.body_len` reflect the fixed header or length prefix. `ulab.record` names the record. `ulab.derived_nonce` shows four zero bytes and the sender sequence, calculated from the header. The frame's observed tags are available as `ulab.header_hmac`, `ulab.control_hmac`, `ulab.data_hmac` and `ulab.aead_tag`. `ulab.handshake_hmac` applies to CH/SH/CF.

A visible OPEN or ACK is expected even in an encrypted item. Filter on DATA when checking a marker. Raw plaintext bytes are in the packet byte pane. Enable TCP reassembly to see a DATA record split across multiple packets. Every structurally decoded record carries `ulab.authentication == "unverified"`. Only the endpoints possess the keys. Mode 0 has no content authentication. The endpoint uses the HMAC in mode 3 to authenticate the visible body.

## Recover visible files, images or text

~~~powershell
python tools/extract_ulab.py experiment.pcapng --out recovered-session-01
~~~

The same command runs with `python3` on Linux. Specify `--tshark PATH`, `--port PORT` or `--iproxy-port PORT` if needed. Use a **new or empty private directory**. The default cumulative staging budget is 1 GiB and accepts an override in bytes with `--max-bytes`. On POSIX, select a local filesystem supporting hard links.

The extractor streams per-packet PDML from TShark. It checks TCP sequence coverage against decoded record sizes, sender sequences, OPEN, mode, chunk index and size, META byte count and the visible END SHA-256. It separates streams, sender directions and item IDs, and strips framing and HMAC trailers. It stages files as `.part` and publishes a complete item under a locally generated name. UTF-8 text uses `.txt`. PNGs identified by MIME and signature use `.png`. Other images and files keep their raw bytes in `.bin`. Remote names never select local paths.

Standard output is JSON Lines. An `extracted` row includes a relative filename, byte count and digest. `encrypted` identifies an encrypted item seen through END, with no content output. `incomplete` identifies missing, malformed or inconsistent data. Exit 0 means the selected traffic yielded complete items or encrypted observations. Exit 1 identifies an incomplete result or absence of complete items. Exit 2 indicates a command, capture-format or filesystem error. A later truncated item can be reported alongside files recovered earlier.

The visible digest establishes consistency for the reconstructed bytes. A matching digest establishes neither source authenticity nor an endpoint save. For delivery evidence, cross-check the independent endpoint logs, its persisted file hash and final ACK. For encrypted images, collect the independent hashes at both endpoints without sharing keys with the Pi.

## Reproduce the synthetic parser checks

~~~powershell
python -m unittest discover -s tests -p test_dissector.py -v
python tests/test_dissector.py --write-synthetic synthetic-ulab.pcap
python tools/extract_ulab.py synthetic-ulab.pcap --out synthetic-output
~~~

The PCAP generator wraps public fixed protocol and enrollment vectors in synthetic IPv4/TCP traffic. It exercises segmented headers, coalesced records, both directions and retransmissions. The enrollment fixture exercises record structure, independently of the ULAB session key in the handshake fixture. No secret provisioning or physical link is tested. The mode-0 and mode-3 PNGs each contain 70 bytes and have SHA-256 `83b264166861967a51a889bc26b8156364d9079c96e900b617e5c54f7431bfb2`. The extended tests also cover empty and multi-chunk content, overlapping opposite-direction transfers, an invalid header or length, a missing DATA record, a changed visible digest and a partial record after earlier complete items.

For independent wired evidence, run these inspection commands on the private PCAPNG from the gadget or Pi loopback. Record its interface, endpoints, capture timing, independent endpoint observations, and raw-file SHA-256.

The command-line and Lua interfaces are documented in the [TShark manual](https://www.wireshark.org/docs/man-pages/tshark.html) and [Wireshark Lua API](https://www.wireshark.org/docs/wsdg_html_chunked/lua_module_Proto.html).
