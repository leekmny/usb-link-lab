# iPhone implementation plan

The app implements the common protocol from commit `78f451c`. The iPhone branch is `ios/native-client`. Coordinate shared changes through the public `leekmny/usb-link-lab` repository. The Windows platform work belongs under `apps/windows/`.

1. Verify both source appendices and commit the verified, byte-exact contract. Add an independent Python fixture generator with deterministic known-answer checks.
2. Implement record and handshake codecs, strict flat JSON schemas, and the Apple session/listener with fragmented reads, independent deadlines, bounded serial writes, and drain. Verify with fixed records and local synthetic peers.
3. Implement staged disk snapshots, durable receive commits, journal/Keychain integration and refusal of public fixture values in live pairing. Verify storage rollback and file boundaries.
4. Implement the SwiftUI conversation, attachment selection, protection selector, details, queue recovery, and settings. Obtain labeled screenshots and run smoke tests on the simulator.
5. Build unsigned arm64 iphoneos output with checksums and provenance. Publish verified artifacts, then track signing, installation, and the physical bidirectional USB acceptance separately.

The initial toolchain decision is GitHub-hosted macOS with an explicitly selected Xcode. Windows provides source editing and Python fixture checks. The project's device installation gate depends on the actual iPhone, Apple driver, signing workflow, and Windows companion build.
