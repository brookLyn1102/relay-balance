// swift-tools-version:5.7
import PackageDescription

let package = Package(
    name: "RelayBalance",
    platforms: [.macOS(.v12)],
    targets: [
        .executableTarget(
            name: "RelayBalance",
            path: "Sources/RelayBalance"
        )
    ]
)
