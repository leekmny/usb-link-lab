# USB pairing for iPhone

## Pair the native apps

Use USBLinkLab 0.2.0 or later on each endpoint and a data cable. After installation,
keep the signed iPhone app open and unlocked. Turn off Wi-Fi, Bluetooth and cellular
data for the USB trial.

1. In Windows USBLinkLab, find and select the USB iPhone. Open App pairing and generate
   an invitation for QR or a six-digit code. The invitation is valid for two minutes.
2. In iPhone USBLinkLab, open App pairing. Choose Scan Windows QR and point the
   in-app camera at the code on the PC. If camera capture is unavailable, choose
   Enter six-digit code and type the code displayed by the corresponding Windows
   invitation, including any leading zeros.
3. Tap Prepare on the iPhone, then Start pairing on Windows. Keep the iPhone app
   in the foreground. The apps save the resulting transfer credential after
   authenticating the peer on the selected USB stream.
4. For a failed or expired attempt, generate a fresh Windows invitation and prepare
   the iPhone again. For an already paired device, reconnect with the saved credential.
   If either endpoint saved a candidate before an interruption and the other did not,
   explicitly enroll again.

The QR represents key material and belongs on the two local app screens. Keep it out
of screenshots and recordings. The six-digit code authorizes one interactive PAKE
attempt. The Keychain credential is the authenticated 32-byte result of enrollment.

## Build on macOS

The [shared enrollment contract](../protocol/PAIRING_V1.md), public fixture and C wrapper
come from the Windows branch. The upstream archive is pinned to Mbed TLS 3.6.7 and
verified by the shared CMake project. The iOS build uses a static XCFramework containing
macOS, arm64 iphoneos and host-architecture simulator slices. The app carries the
[Mbed TLS license](../apps/ios/App/MbedTLS-3.6.7-LICENSE.txt).

~~~sh
bash apps/ios/scripts/build-pairing-native.sh
swift test --package-path apps/ios
bash apps/ios/scripts/build-unsigned.sh
~~~

Use Xcode 16.4 for the scripted device build and Xcode 26.3 with the iOS 26.2 runtime
for the simulator job. The script requires CMake 3.24 or later and the selected Xcode.
CMake outputs and the generated XCFramework live under ignored build directories.

The hosted companion test uses the production Windows Python protocol code with
the same C bridge compiled for macOS and a loopback Swift server. The fixture suite,
simulator camera-unavailable flow, and cabled signed-phone trials establish separate
evidence. The device checklist covers QR scanning, camera denial, leading-zero PIN,
wrong-code then fresh invitation, expiry or cancellation, and saved-key reconnect.
Use synthetic transfer data after pairing. Record the hardware runs in the
[verification matrix](ios-verification-matrix.md).
