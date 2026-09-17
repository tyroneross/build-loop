// swift-tools-version: 5.9
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//
// Build-loop native AX driver — drives running macOS apps through the
// Accessibility API. Vendored from the same source IBR uses; both projects
// can keep their own copy without depending on each other.

import Foundation
import PackageDescription

var targets: [Target] = [
    .executableTarget(
        name: "bl-ax-driver",
        path: "Sources"
    ),
]

// SwiftPM rejects a target whose path is missing, so the test target is
// declared only when Tests/ exists (a trimmed vendored copy still builds).
let testsDir = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("Tests").path
if FileManager.default.fileExists(atPath: testsDir) {
    targets.append(.testTarget(name: "bl-ax-driver-tests", dependencies: ["bl-ax-driver"], path: "Tests"))
}

let package = Package(
    name: "bl-ax-driver",
    platforms: [.macOS(.v13)],
    targets: targets
)
