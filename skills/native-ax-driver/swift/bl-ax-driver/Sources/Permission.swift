// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
import ApplicationServices
import Foundation

// MARK: - Accessibility permission: ask at most once per user
//
// macOS shows the "Open System Settings" Accessibility dialog every time a
// process calls AXIsProcessTrustedWithOptions with prompt=true while untrusted.
// The driver used to do that on every scan/action, so an untrusted host
// terminal saw the dialog over and over.
//
// Policy:
//   - Every trust check uses prompt=false.
//   - The dialog is shown only when the caller passes --request-permission AND
//     no record exists in ~/.build-loop/permissions.json (user-level, shared by every
//     project and worktree). Prompting writes the record first.
//   - Untrusted runs exit with `accessibilityUntrustedExitCode` and a stderr
//     message naming the System Settings path and the explicit re-request step.

/// Distinct exit code for "Accessibility not granted" (EX_NOPERM).
let accessibilityUntrustedExitCode: Int32 = 77

let requestPermissionCommand = "python3 ${CLAUDE_PLUGIN_ROOT}/skills/native-ax-driver/scripts/native_driver.py request-permission"

enum AccessibilityPromptDecision: Equatable {
    /// Process is already trusted; proceed.
    case trusted
    /// Show the macOS dialog once and record that it was shown.
    case prompt
    /// Untrusted; exit without showing any dialog.
    case failWithoutPrompt
}

/// Pure decision: prompt only when untrusted, explicitly requested, and never asked before.
func accessibilityPromptDecision(trusted: Bool, alreadyAsked: Bool, promptRequested: Bool) -> AccessibilityPromptDecision {
    if trusted { return .trusted }
    if promptRequested && !alreadyAsked { return .prompt }
    return .failWithoutPrompt
}

func defaultPermissionRecordURL() -> URL {
    let home = ProcessInfo.processInfo.environment["HOME"].map { URL(fileURLWithPath: $0) }
        ?? FileManager.default.homeDirectoryForCurrentUser
    return home.appendingPathComponent(".build-loop").appendingPathComponent("permissions.json")
}

/// The `accessibility.askedAt` timestamp, or nil when no record exists.
/// An unreadable or malformed file counts as "no record".
func accessibilityAskedAt(recordURL: URL) -> String? {
    guard let data = try? Data(contentsOf: recordURL),
          let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let entry = root["accessibility"] as? [String: Any],
          let askedAt = entry["askedAt"] as? String else {
        return nil
    }
    return askedAt
}

/// Write `accessibility.askedAt`, preserving any other keys already in the file.
func recordAccessibilityPrompt(recordURL: URL, now: Date = Date()) throws {
    var root: [String: Any] = [:]
    if let data = try? Data(contentsOf: recordURL),
       let existing = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
        root = existing
    }
    let formatter = ISO8601DateFormatter()
    root["accessibility"] = ["askedAt": formatter.string(from: now)]
    try FileManager.default.createDirectory(
        at: recordURL.deletingLastPathComponent(), withIntermediateDirectories: true)
    let data = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: recordURL, options: .atomic)
}

private let grantInstruction =
    "Grant Accessibility to the app that launched this command (your terminal or IDE) in " +
    "System Settings > Privacy & Security > Accessibility, then quit and reopen that app and re-run."

func accessibilityUntrustedMessage(askedAt: String?, recordURL: URL) -> String {
    if let askedAt = askedAt {
        return "Error: Accessibility permission required. build-loop already showed the macOS permission prompt " +
            "(\(askedAt)) and will not show it again. \(grantInstruction) " +
            "To re-request the prompt explicitly: rm \(recordURL.path) && \(requestPermissionCommand)"
    }
    return "Error: Accessibility permission required. build-loop does not open the macOS permission prompt " +
        "automatically. \(grantInstruction) To show the prompt once: \(requestPermissionCommand)"
}

func accessibilityPromptedMessage(recordURL: URL) -> String {
    return "Accessibility permission required. build-loop showed the macOS permission prompt once and recorded it " +
        "in \(recordURL.path); it will not ask again. \(grantInstruction)"
}

/// Side-effecting gate used by the one-shot entry point. Returns only when trusted;
/// otherwise writes the stderr message and exits with `accessibilityUntrustedExitCode`.
func requireAccessibilityTrust(promptRequested: Bool, recordURL: URL = defaultPermissionRecordURL()) {
    let noPrompt = [kAXTrustedCheckOptionPrompt.takeUnretainedValue(): false] as CFDictionary
    let trusted = AXIsProcessTrustedWithOptions(noPrompt)
    let askedAt = trusted ? nil : accessibilityAskedAt(recordURL: recordURL)

    switch accessibilityPromptDecision(trusted: trusted, alreadyAsked: askedAt != nil, promptRequested: promptRequested) {
    case .trusted:
        return
    case .failWithoutPrompt:
        fputs(accessibilityUntrustedMessage(askedAt: askedAt, recordURL: recordURL) + "\n", stderr)
        exit(accessibilityUntrustedExitCode)
    case .prompt:
        do {
            try recordAccessibilityPrompt(recordURL: recordURL)
        } catch {
            fputs("Warning: could not write \(recordURL.path): \(error.localizedDescription)\n", stderr)
        }
        let prompt = [kAXTrustedCheckOptionPrompt.takeUnretainedValue(): true] as CFDictionary
        if AXIsProcessTrustedWithOptions(prompt) { return }
        fputs(accessibilityPromptedMessage(recordURL: recordURL) + "\n", stderr)
        exit(accessibilityUntrustedExitCode)
    }
}
