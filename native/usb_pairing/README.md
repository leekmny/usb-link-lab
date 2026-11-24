# USBLinkLab native PIN pairing

The wrapper uses the stock Mbed TLS 3.6.7 EC J-PAKE API with P-256 and SHA-256. CMake checks the SHA-256 of the official release archive before extracting it. The Mbed TLS license is in the Windows notices directory. [Source and upstream checksum](https://github.com/Mbed-TLS/mbedtls/releases/tag/mbedtls-3.6.7).

The application sends the round messages in its own pairing records. Treat the stock round messages as opaque byte strings of at most 512 bytes. Their wire format follows the Mbed TLS implementation for Thread 1.0, with its client and server roles. The application completes mutual key confirmation before storing its derived pairing secret. [Mbed TLS configuration and EC J-PAKE API](https://github.com/Mbed-TLS/mbedtls/tree/mbedtls-3.6.7/include/mbedtls).

## Windows x64

From the repository root, run `./apps/windows/windows.ps1 pairing-native`. Setup, test, and package also run this step. It requires `uv` and either Visual Studio 2022 with the x64 C++ build tools and Windows SDK, or MinGW-w64 GCC targeting x64 Windows. If needed, set `ULAB_MINGW_BIN` to the MinGW-w64 compiler's `bin` directory. The script pins CMake 3.31.10 and uses Ninja 1.13.0 with MinGW. The DLL is staged in `apps/windows/usblinklab/_native` and included under the same package directory in the portable bundle.

## iOS arm64

On a Mac, select the desired Xcode in `DEVELOPER_DIR`. The example uses Xcode 16.4, the iOS/iOS Simulator 18.5 SDKs, CMake 3.31.10, and a minimum deployment target of iOS 17.0. Build device and simulator slices separately. These commands run from the repository root.

```sh
xcodebuild -version
xcrun --sdk iphoneos --show-sdk-version
xcrun --sdk iphonesimulator --show-sdk-version

for sdk in iphoneos iphonesimulator; do
    build=".build/ulab-pairing-${sdk}"
    cmake -S native/usb_pairing -B "$build" -G Xcode \
        -DCMAKE_SYSTEM_NAME=iOS \
        -DCMAKE_OSX_SYSROOT="$sdk" \
        -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DCMAKE_OSX_DEPLOYMENT_TARGET=17.0 \
        -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY \
        -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO
    cmake --build "$build" --config Release --target ulab_pairing
done

mkdir -p .build/ulab-pairing-headers
cp native/usb_pairing/ulab_pairing.h native/usb_pairing/module.modulemap \
    .build/ulab-pairing-headers/
xcodebuild -create-xcframework \
    -library .build/ulab-pairing-iphoneos/libulab_pairing_complete.a \
    -headers .build/ulab-pairing-headers \
    -library .build/ulab-pairing-iphonesimulator/libulab_pairing_complete.a \
    -headers .build/ulab-pairing-headers \
    -output .build/ULABPairing.xcframework
```

Link the static XCFramework in the Xcode target and import `ULABPairing` from Swift. Pass the six ASCII digits as bytes. Use a new context for each attempt, call read and write for round 1, then read and write for round 2. Read and write can occur in either order in a round. Check each result, derive the 32-byte output once, and free the context on all paths. The iOS commands require separate verification with Xcode and a physical iPhone.
