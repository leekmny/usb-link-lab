# Wired transport and evidence boundaries

Windows acts as ULAB client and iPhone acts as ULAB server. The Pi terminates two TCP connections and copies bytes between them. Its downstream USB host connection uses Apple multiplexing to reach the existing signed foreground app's IPv4 loopback listener on port `43123`. The authenticated session is end to end. Use the root diagram for the cabling and the Pi service guide for commands. [Diagram](../README.md) · [Pi service](relay-service.md)

## Following one item

1. In Windows, select **Raspberry Pi USB relay** and enter the Pi's gadget IPv4 address and front port. The endpoint checks the current Windows route, source address, live hardware interface and USB PnP vendor/product, presents the candidate to the operator and rechecks before connecting. The socket uses the selected source IPv4 and Winsock `IP_UNICAST_IF`. It verifies its connected endpoints and the OS interface index. Management DHCP addresses or default routes cannot act as application destinations. The default front port is `43123`. [Windows adapter](../apps/windows/usblinklab/pi_transport.py)
2. On the Pi, the listener binds the configured gadget address and interface and allows the configured Windows source. It opens one connection to `127.0.0.1:43124` by default. A USB-only iproxy maps it to the selected iPhone's `43123`. The local usbmuxd service needs an owner-approved Apple host relationship to the iPhone. Starting a second probe on the application port can occupy the only session. [Service and connection lifecycle](relay-service.md)
3. Windows and iPhone perform their existing ULAB exchange. QR enrollment uses an optically conveyed candidate. Six-digit enrollment uses the existing EC J-PAKE exchange on the same stream before the ordinary handshake. Existing saved pairs use the ordinary handshake directly. Follow the checked-in pairing document for the wire format. Its direct-USB deployment wording describes the original physical connection. This integration extends the path between the same endpoint roles. [Enrollment bytes and state](../protocol/PAIRING_V1.md)
4. The sender snapshots the selected mode when queuing each item. A complete acknowledgment follows durable receiver storage. If the receiver commits and the final ACK is lost, the sender can report unconfirmed delivery. Check the receiver before explicitly retrying. An interrupted item has no byte-resume protocol. [Frame and transfer contract](../protocol/PROTOCOL_V1.md)

The two deployment modes have independent Windows credential storage by default. Explicit **Use the saved direct iPhone pairing** permits reconnecting the original authenticated logical peer over the gadget. This option uses the former peer's credential and checks the handshake before releasing eligible queued data. An independent relay enrollment uses a separate local slot. Changing either IP address moves the transport without assigning a queue or identifying a phone. [Profile policy and setup](../apps/windows/README.md)

## What an observer can establish

| Observation point | Scope | Interpretation |
| --- | --- | --- |
| Pi gadget network interface | Configured Windows and Pi addresses, TCP front port | IP/TCP timing, lengths, ULAB session and item record boundaries with TCP reassembly |
| Pi loopback | TCP to the configured iproxy port | Independent TCP sequence space carrying the same ordered application byte stream |
| Pi USB host bus | Selected iPhone device address on the current bus, with explicit private capture selection | Host-controller requests and completions. Data may be split, encapsulated, coalesced or truncated. A physical-layer/electrical trace requires separate instrumentation. |
| Pi service diagnostics | Local session ID, direction, byte counts and forwarding status | Operational evidence. Endpoint receipts, content authenticity and durable storage require endpoint evidence. |

The relay performs no per-item decoding to forward bytes. The Wireshark dissector parses a captured stream separately. An unpaired observer can see modes, sizes, sequence numbers, item IDs, direction and timing. Its interpretation of control messages and tags is unauthenticated. OPEN and ACK remain observable in encrypted experiments. Compare the decoded DATA body for the same synthetic item and direction. [Dissector fields](ulab-dissector.md)

| ULAB mode | Visible DATA | Endpoint check |
| --- | --- | --- |
| 0 · Plaintext | Original bytes | Header/control protection and a visible END digest. A changed body and matching digest can agree. |
| 1 · AES-256-GCM | Ciphertext and authentication tag | GCM tag validated with an endpoint key |
| 2 · ChaCha20-Poly1305 | Ciphertext and authentication tag | Poly1305 tag validated with an endpoint key |
| 3 · HMAC-SHA256 | Original bytes and HMAC | Content HMAC validated with an endpoint key |

Both encrypted modes derive their nonce from protocol state. The dissector can display the derivation, while the authentication keys stay on Windows and iPhone. A missing known marker in a capture is a measurement of that capture. Pair it with an endpoint decryption test and a controlled invalid-tag rejection. Compromise of the long-term ULAB pairing secret permits analysis of previously recorded sessions. [Cryptographic contract](../protocol/PROTOCOL_V1.md)

## Trust and failure diagnosis

Apple host trust lets the Pi use iOS multiplexing and may create Apple pairing files on the Pi. ULAB QR material, PINs, PSKs, Keychain/DPAPI data and session keys belong to the endpoints. The dedicated relay has no key import, crypto configuration or application ACK generation. Keep signed apps, Apple pairing records, credentials and raw captures out of public evidence. A process with local administrative access to the Pi can observe plaintext-mode traffic and the capture service's files.

| Symptom | Check | Next action |
| --- | --- | --- |
| Windows displays a COM port or cannot find the gadget | Check official gadget enablement, the Pi UDC state, the Windows Pi RNDIS driver and the splitter's data branch | Restore the independent management connection before changing or rebooting the gadget. |
| A Pi hostname works for SSH but discovery rejects the app route | Inspect the full unforced `Find-NetRoute` result, including source, interface, direct prefix and next hop | Enter the numeric gadget address. Preserve the existing system routing. |
| Linux sees Apple USB hardware and usbmux lists zero devices | Check usbmuxd status and whether the desktop camera/photo import has mounted this phone | Close the import window and release only the selected phone camera mount. Unlock and approve Apple Trust, then retry the configured USB-only selection. |
| iproxy is active but registration does not connect | Check the exact signed foreground build, current host trust and the iPhone's saved key or prepared invitation | Refresh the invitation at both endpoints. Avoid repeated diagnostic TCP probes on the app port. |
| Wireshark shows TCP with no ULAB frames | Check the interface, TCP port, Lua plugin and start of the handshake | Start capture before a fresh saved-key connection. For a custom port, configure the matching decode mapping. |
| The dashboard shows unavailable telemetry or packet counts | Check selected interface, collector availability, permissions and the session's timestamps | Preserve unavailable values and reported drops. A state counter alone cannot establish delivery. |
| A cable interruption stops administration too | Verify management route and SSH source independently | Restore management using the Pi screen or a separate verified interface. Resume the endpoint session after both data legs recover. |

For remote observation, send only capture/control data over SSH. Use an independent management route when available; otherwise restrict the capture to the experiment ports and keep SSH out of the filter. For raw USB observations reset the selected device address after re-enumeration. Review sensitive enrollment and USB traffic locally. [Capture and demo steps](ko-touch-lab.md) · [Acceptance record](acceptance.md)
