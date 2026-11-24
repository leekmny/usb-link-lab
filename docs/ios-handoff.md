# iPhone build and Windows handoff

- Shared repository: [leekmny/usb-link-lab](https://github.com/leekmny/usb-link-lab)
- iPhone: [ios/native-client](https://github.com/leekmny/usb-link-lab/tree/ios/native-client); Windows: `platform/windows`
- Common baseline: [78f451cd2a0e992defc4bd27af212f3ab538747f](https://github.com/leekmny/usb-link-lab/commit/78f451cd2a0e992defc4bd27af212f3ab538747f)
- Contract: `ULAB1-20261124-R1`; SHA-256 `535a818b4e76c82bf7c782f9aa9482730b13865e1ddb8c3fb0cba2c946a6596f`
- App bundle ID: `org.usblinklab.ios`; deployment target: iOS 17; device architecture: arm64
- Source: `apps/ios/USBLinkLab.xcodeproj`, `App.xcconfig`, and local `USBLinkCore` Swift package. The app and core use Apple platform frameworks with zero external Swift packages.
- Device release: [iPhone 0.1.0 unsigned prerelease](https://github.com/leekmny/usb-link-lab/releases/tag/ios-v0.1.0); build commit `088a335389a33429d14eccce229ed390d1572d1b`.
- Evidence: [verification matrix](ios-verification-matrix.md) and [iPhone workflow](https://github.com/leekmny/usb-link-lab/actions/workflows/ios.yml)

## Build and verify

On Windows, from the repository root:

~~~powershell
python -m pip install -r tools/fixtures/requirements.txt
python tools/fixtures/generate_ulab1.py --check
~~~

On macOS, use Xcode 16.4 for core and device builds and Xcode 26.3 with iOS 26.2 for simulator tests:

~~~sh
swift test --package-path apps/ios
xcodebuild -project apps/ios/USBLinkLab.xcodeproj -scheme USBLinkLab \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro Max,OS=26.2' test
bash apps/ios/scripts/build-unsigned.sh
~~~

The device build verifies iphoneos, arm64, iOS 17, bundle ID, and the absence of a signature or embedded provisioning profile. The IPA contains `Payload/USBLinkLab.app`. Compare `SHA256SUMS.txt` with `Get-FileHash -Algorithm SHA256 USBLinkLab-iphoneos-arm64-unsigned.ipa` on Windows and read `build-provenance.txt`. Keep signed devices' provisioning output local.

## Windows-only signing and installation

The selected Windows path is [Sideloadly](https://sideloadly.io/) over USB. Its Windows documentation calls for Apple-distributed iTunes and iCloud and advertises iOS 27 support. [AltStore Classic](https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows) requires AltServer/AltStore and Apple prerequisites. The user's model and version are iPhone 17 Pro Max and iOS 27.0.1. Windows detected the phone via Apple Mobile Device Support 20.0.1.47, and the user confirmed that Sideloadly 0.72 showed the connected device. The tethered signing, installation, launch and application-session checks are tracked below.

1. Connect the iPhone with a data cable, unlock it and complete the [Trust This Computer](https://support.apple.com/en-us/109054) prompts.
2. Install the vendor-documented Windows prerequisites if absent and confirm the phone appears in the Apple application. Confirm with the device owner before replacing an existing Apple installation.
3. In Sideloadly select the tethered iPhone, open the unsigned IPA, and start signing/installing. Enter Apple credentials and approval codes in the local signing application.
4. If prompted, enable **Settings → Privacy & Security → Developer Mode**, restart and confirm with the device passcode. Follow the developer trust prompt if shown. Initial signing or trust may require Internet access.
5. Launch USBLinkLab with the phone unlocked. Open pairing, generate the application secret at one endpoint and enter it locally at the other. With a free Apple Personal Team, renew provisioning/reinstallation before the seven-day profile expiry.

## Physical USB acceptance

The Windows endpoint selects the plugged-in device via the Apple service, then connects to device port `43123`. When testing iproxy separately, inspect `iproxy --help` and select the exact tethered device using only `--local`. If USB was attached to WSL, use `usbipd list` and detach the actual bus ID before the native Windows test.

Disable wireless transports for acceptance. Transfer a Korean message, a synthetic image and an ordinary file in each direction in all four modes. Compare source and saved SHA-256 values. Also try an empty file, a chunk boundary, backgrounding, session cancellation, and unplugging while an item is active. Preserve metadata-only evidence. Verify the completion ACK in ordinary operation and correct interruption statuses after disconnect.

Disconnect after END can leave a saved receiver item with delivery unconfirmed on the sender. A manual retry through a new authenticated session can create a duplicate. USB-only host selection and the cable-pull test establish the transport. The loopback listener itself has no physical-link metadata.

## Reproducible research measurements

Direct USB provides the baseline for any later Pi measurement. Use the same deterministic
files, per-mode ordering, cable, USB port and app commits in both phases. Record iOS,
Windows and Apple service versions. For each run preserve direction, mode, source byte
count, source and saved SHA-256, completion status, and whether the cable stayed plugged in.
Separate staging time, handshake time and elapsed time from OPEN to complete ACK.
Compute payload throughput as saved bytes divided by the OPEN-to-complete interval.
For each condition report repetitions, median and range as well as interrupted attempts;
omit throughput for transfers without a confirmed final ACK.

Use the named plaintext and authenticated-mode fixture tests to check that content
observability and integrity claims agree with the protocol. Plaintext and HMAC carry
original bytes at the application stream boundary. The AEAD modes protect those bytes;
frame lengths, modes, IDs and timing remain visible. The prefix/header tag and complete
ACK authenticate their own fields independently of a Plaintext body's digest.

The Python companion conformance test uses the real Windows Session and Storage
modules against the Swift loopback endpoint. Its synthetic local TCP result is recorded
separately from physical USB and from any future Pi measurements.
