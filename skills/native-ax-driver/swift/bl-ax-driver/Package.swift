// swift-tools-version: 5.9
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
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
