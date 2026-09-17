// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
import XCTest
@testable import bl_ax_driver

final class PermissionTests: XCTestCase {
    private var dir: URL!
    private var recordURL: URL { dir.appendingPathComponent(".build-loop/permissions.json") }

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("bl-perm-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: dir)
    }

    func testTrustedNeverPrompts() {
        for asked in [false, true] {
            for requested in [false, true] {
                XCTAssertEqual(accessibilityPromptDecision(trusted: true, alreadyAsked: asked, promptRequested: requested), .trusted)
            }
        }
    }

    func testUntrustedPromptsOnlyWhenRequestedAndNeverAsked() {
        XCTAssertEqual(accessibilityPromptDecision(trusted: false, alreadyAsked: false, promptRequested: true), .prompt)
        XCTAssertEqual(accessibilityPromptDecision(trusted: false, alreadyAsked: false, promptRequested: false), .failWithoutPrompt)
        XCTAssertEqual(accessibilityPromptDecision(trusted: false, alreadyAsked: true, promptRequested: true), .failWithoutPrompt)
        XCTAssertEqual(accessibilityPromptDecision(trusted: false, alreadyAsked: true, promptRequested: false), .failWithoutPrompt)
    }

    func testMissingOrMalformedRecordCountsAsNotAsked() throws {
        XCTAssertNil(accessibilityAskedAt(recordURL: recordURL))
        try FileManager.default.createDirectory(at: recordURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("not json".utf8).write(to: recordURL)
        XCTAssertNil(accessibilityAskedAt(recordURL: recordURL))
    }

    func testRecordRoundTripsAndPreservesOtherKeys() throws {
        try FileManager.default.createDirectory(at: recordURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(#"{"screenRecording":{"askedAt":"x"}}"#.utf8).write(to: recordURL)

        try recordAccessibilityPrompt(recordURL: recordURL, now: Date(timeIntervalSince1970: 0))

        XCTAssertEqual(accessibilityAskedAt(recordURL: recordURL), "1970-01-01T00:00:00Z")
        let root = try JSONSerialization.jsonObject(with: Data(contentsOf: recordURL)) as? [String: Any]
        XCTAssertNotNil(root?["screenRecording"])
        // Once recorded, a second explicit request must not prompt.
        XCTAssertEqual(accessibilityPromptDecision(trusted: false, alreadyAsked: accessibilityAskedAt(recordURL: recordURL) != nil, promptRequested: true), .failWithoutPrompt)
    }

    func testMessagesNameSettingsPathAndReRequestStep() {
        let fresh = accessibilityUntrustedMessage(askedAt: nil, recordURL: recordURL)
        XCTAssertTrue(fresh.contains("Privacy & Security > Accessibility"))
        XCTAssertTrue(fresh.contains("native_driver.py request-permission"))

        let asked = accessibilityUntrustedMessage(askedAt: "2026-09-17T00:00:00Z", recordURL: recordURL)
        XCTAssertTrue(asked.contains("will not show it again"))
        XCTAssertTrue(asked.contains("rm \(recordURL.path)"))
    }
}
