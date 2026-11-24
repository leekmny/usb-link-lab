#!/usr/bin/env bash
set -euo pipefail

ios_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(git -C "$ios_dir" rev-parse --show-toplevel)"
output_dir="${1:-$repo_dir/artifacts/ios}"
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
temporary="$(mktemp -d "${TMPDIR:-/tmp}/usblinklab-ios.XXXXXXXX")"
trap 'rm -rf -- "$temporary"' EXIT
export LC_ALL=C

contract="$repo_dir/protocol/PROTOCOL_V1.md"
contract_hash="$(shasum -a 256 "$contract" | awk '{print $1}')"
[[ "$contract_hash" == 535a818b4e76c82bf7c782f9aa9482730b13865e1ddb8c3fb0cba2c946a6596f ]]

xcodebuild -quiet -project "$ios_dir/USBLinkLab.xcodeproj" -scheme USBLinkLab \
    -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
    -derivedDataPath "$temporary/Build" ARCHS=arm64 CODE_SIGNING_ALLOWED=NO build

app="$temporary/Build/Build/Products/Release-iphoneos/USBLinkLab.app"
binary="$app/USBLinkLab"
[[ "$(xcrun lipo -archs "$binary")" == arm64 ]]
[[ "$(plutil -extract DTPlatformName raw -o - "$app/Info.plist")" == iphoneos ]]
[[ "$(plutil -extract MinimumOSVersion raw -o - "$app/Info.plist")" == 17.0 ]]
[[ "$(plutil -extract CFBundleIdentifier raw -o - "$app/Info.plist")" == org.usblinklab.ios ]]
[[ ! -e "$app/embedded.mobileprovision" && ! -d "$app/_CodeSignature" ]]
if codesign --display "$app" >/dev/null 2>&1; then
    echo "Unexpected signature on the unsigned device output" >&2
    exit 1
fi

mkdir -p "$temporary/Payload"
ditto "$app" "$temporary/Payload/USBLinkLab.app"
ipa=USBLinkLab-iphoneos-arm64-unsigned.ipa
ditto -c -k --norsrc --keepParent "$temporary/Payload" "$temporary/$ipa"
unzip -tq "$temporary/$ipa"
mv "$temporary/$ipa" "$output_dir/$ipa"
(
    cd "$output_dir"
    shasum -a 256 "$ipa" > SHA256SUMS.txt
)
{
    echo "Commit: $(git -C "$repo_dir" rev-parse HEAD)"
    echo "Protocol: ULAB1-20261124-R1"
    echo "Contract SHA-256: $contract_hash"
    echo "Runner image: ${ImageOS:-local} ${ImageVersion:-local}"
    echo "OS: $(sw_vers -productVersion) ($(sw_vers -buildVersion))"
    echo "Host architecture: $(uname -m)"
    echo "Target: iphoneos arm64, iOS 17.0+, org.usblinklab.ios"
    echo "Signing: unsigned, needs development signing and provisioning"
    echo "Swift dependencies: local USBLinkCore; Apple SDK frameworks only"
    xcodebuild -version
    echo "SDK: $(xcrun --sdk iphoneos --show-sdk-version)"
    xcrun swift --version
    xcrun vtool -show-build "$binary"
} > "$output_dir/build-provenance.txt"
echo "Device artifact: $output_dir/$ipa"
