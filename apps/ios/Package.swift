// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "USBLinkCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "USBLinkCore", targets: ["USBLinkCore"])],
    targets: [
        .binaryTarget(name: "ULABPairing", path: "Generated/ULABPairing.xcframework"),
        .target(name: "USBLinkCore", dependencies: ["ULABPairing"]),
        .testTarget(name: "USBLinkCoreTests", dependencies: ["USBLinkCore", "ULABPairing"])
    ],
    swiftLanguageModes: [.v5]
)
