// swift-tools-version: 5.9
//
// Build-loop native AX driver — drives running macOS apps through the
// Accessibility API. Vendored from the same source IBR uses; both projects
// can keep their own copy without depending on each other.

import PackageDescription

let package = Package(
    name: "bl-ax-driver",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "bl-ax-driver",
            path: "Sources"
        ),
    ]
)
