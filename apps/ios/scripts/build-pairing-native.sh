#!/usr/bin/env bash
set -euo pipefail

ios_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(git -C "$ios_dir" rev-parse --show-toplevel)"
generated="$ios_dir/Generated"
work="$ios_dir/.build/ulab-pairing"
source="$repo_dir/native/usb_pairing"
host_arch="$(uname -m)"

if [[ "$host_arch" != arm64 && "$host_arch" != x86_64 ]]; then
    printf 'Unsupported macOS host architecture %s\n' "$host_arch" >&2
    exit 1
fi

cmake --version
xcodebuild -version
mkdir -p "$work/headers" "$generated"
cp "$source/ulab_pairing.h" "$source/module.modulemap" "$work/headers/"

# The shared CMake project downloads and verifies the pinned upstream archive.
# Use the same download for each slice in this host build.
common=(-S "$source" -G 'Unix Makefiles' -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY
    "-DFETCHCONTENT_BASE_DIR=$work/downloads")
mac="$work/macos"
cmake "${common[@]}" -B "$mac" -DCMAKE_OSX_SYSROOT=macosx \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 "-DCMAKE_OSX_ARCHITECTURES=$host_arch"
cmake --build "$mac" --target ulab_pairing --parallel 4
xcrun libtool -static -o "$mac/libulab_pairing_complete.a" \
    "$mac/libulab_pairing.a" "$work/downloads/mbedtls-build/library/libmbedcrypto.a"

for sdk in iphoneos iphonesimulator; do
    arch=arm64
    if [[ "$sdk" == iphonesimulator ]]; then arch="$host_arch"; fi
    slice="$work/$sdk"
    # FetchContent's build directory is per slice. Its read-only source is reused.
    sdk_path="$(xcrun --sdk "$sdk" --show-sdk-path)"
    cmake -S "$source" -B "$slice" -G 'Unix Makefiles' -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_SYSTEM_NAME=iOS "-DCMAKE_OSX_SYSROOT=$sdk_path" "-DCMAKE_OSX_ARCHITECTURES=$arch" \
        -DCMAKE_OSX_DEPLOYMENT_TARGET=17.0 -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY \
        "-DFETCHCONTENT_SOURCE_DIR_MBEDTLS=$work/downloads/mbedtls-src"
    cmake --build "$slice" --config Release --target ulab_pairing --parallel 4
    [[ "$(xcrun lipo -archs "$slice/libulab_pairing_complete.a")" == "$arch" ]]
done

# Keep a temporary path on the same volume so the generated package is published
# only after xcodebuild successfully assembles it.
scratch="$(mktemp -d "$generated/pairing.XXXXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT
xcodebuild -create-xcframework \
    -library "$mac/libulab_pairing_complete.a" -headers "$work/headers" \
    -library "$work/iphoneos/libulab_pairing_complete.a" -headers "$work/headers" \
    -library "$work/iphonesimulator/libulab_pairing_complete.a" -headers "$work/headers" \
    -output "$scratch/ULABPairing.xcframework"
plutil -lint "$scratch/ULABPairing.xcframework/Info.plist"
rm -rf -- "$generated/ULABPairing.xcframework"
mv "$scratch/ULABPairing.xcframework" "$generated/ULABPairing.xcframework"

# For the macOS-only conformance test, load the Windows Python implementation's
# actual ctypes bridge calls through the same native C archive.
xcrun clang -arch "$host_arch" -mmacosx-version-min=13.0 -dynamiclib \
    -Wl,-force_load,"$mac/libulab_pairing_complete.a" \
    -o "$generated/libulab_pairing_test.dylib"
printf 'Built ULABPairing for iphoneos arm64, iphonesimulator %s, and macOS %s\n' "$host_arch" "$host_arch"
