// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "USBLinkCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "USBLinkCore", targets: ["USBLinkCore"])],
    targets: [
        .target(name: "USBLinkCore"),
        .testTarget(name: "USBLinkCoreTests", dependencies: ["USBLinkCore"])
    ],
    swiftLanguageModes: [.v5]
)
