# Windows USBLinkLab Implementation Plan

**Goal** Build the Windows endpoint and produce a reproducible Windows package for the shared ULAB1 project.

**Specification** The supplied Windows brief and the canonical contract in `protocol/PROTOCOL_V1.md`. Contract revision ULAB1-20261124-R1 is committed first.

- [x] Assert the supplied handshake and frame vectors, then implement the strict direction-specific frame codec and body schemas. Publish deterministic records for both directions.
- [x] Test and build the USB-only transport and diagnostic probe. Check Windows device support, select a device explicitly, and keep the test stream out of the normal app entry point.
- [x] Test and implement streamed snapshots, authenticated duplex state transitions, durable receive commits, queue admission, cancellation, reconnect, and drain.
- [x] Exercise the Windows desktop UI against the isolated test peer. Verify pairing, mode snapshots, file review, history and previews, diagnostics, and localized layouts.
- [ ] Build and smoke-test the portable EXE/ZIP, record licenses and checksums, publish to the confirmed repository, and verify the remote commit and assets.
- [ ] Check the real cable path when the signed foreground iPhone listener is available. Record device, Swift interop, wireless-disabled and unplug outcomes separately.

Review focus includes late final ACKs, modified source data while staging, network-only device selections, committed files when acknowledgment is lost, and shutdown during concurrent transfers.
