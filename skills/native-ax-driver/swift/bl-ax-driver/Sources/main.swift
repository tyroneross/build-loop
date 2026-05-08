// Build-loop native AX driver
//
// Reads a running macOS app's accessibility tree and dispatches AX actions
// (AXPress, AXShowMenu, AXSetValue, AXFocus, AXIncrement, AXDecrement,
// AXConfirm, AXCancel, AXScrollToVisible) on individual elements.
//
// Provenance: ported from interface-built-right's ibr-ax-extract
// (src/native/swift/ibr-ax-extract/Sources/main.swift). Kept binary-compatible
// with that tool's output schema so existing parsers work unchanged. Build-loop
// vendors its own copy so this capability is built in, not bridged.
//
// All actions go through AXUIElement APIs. The driver does NOT post CGEvent
// mouse/keyboard events — the user's hardware cursor is never moved.

import AppKit
import Foundation

// MARK: - Element Model

struct AXExtractedElement: Codable {
    let role: String
    let subrole: String?
    let title: String?
    let description: String?
    let identifier: String?
    let value: String?
    let enabled: Bool
    let focused: Bool
    let actions: [String]
    let position: Position?
    let size: Size?
    let children: [AXExtractedElement]
    let path: [Int]  // Index path from app root: [0, 2, 1] = 1st child → 3rd child → 2nd child

    struct Position: Codable {
        let x: Double
        let y: Double
    }

    struct Size: Codable {
        let width: Double
        let height: Double
    }
}

/// Legacy output format for simulator scanning (backwards compatible)
struct LegacyElement: Codable {
    let identifier: String
    let label: String
    let role: String
    let traits: [String]
    let frame: Frame
    let isEnabled: Bool
    let value: String?
    let path: [Int]
    let children: [LegacyElement]

    struct Frame: Codable {
        let x: Double
        let y: Double
        let width: Double
        let height: Double
    }
}

// MARK: - AX Attribute Helpers

func getStringAttribute(_ element: AXUIElement, _ attribute: String) -> String? {
    var value: AnyObject?
    let result = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard result == .success, let str = value as? String else { return nil }
    return str
}

func getBoolAttribute(_ element: AXUIElement, _ attribute: String) -> Bool {
    var value: AnyObject?
    let result = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard result == .success else { return false }
    if let num = value as? NSNumber { return num.boolValue }
    return false
}

func getPosition(_ element: AXUIElement) -> AXExtractedElement.Position? {
    var posValue: AnyObject?
    let result = AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString, &posValue)
    guard result == .success, let val = posValue else { return nil }
    var position = CGPoint.zero
    AXValueGetValue(val as! AXValue, .cgPoint, &position)
    return AXExtractedElement.Position(x: Double(position.x), y: Double(position.y))
}

func getSize(_ element: AXUIElement) -> AXExtractedElement.Size? {
    var sizeValue: AnyObject?
    let result = AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString, &sizeValue)
    guard result == .success, let val = sizeValue else { return nil }
    var size = CGSize.zero
    AXValueGetValue(val as! AXValue, .cgSize, &size)
    return AXExtractedElement.Size(width: Double(size.width), height: Double(size.height))
}

func getActions(_ element: AXUIElement) -> [String] {
    var actionsValue: CFArray?
    let result = AXUIElementCopyActionNames(element, &actionsValue)
    guard result == .success, let actions = actionsValue as? [String] else { return [] }
    return actions
}

func getChildren(_ element: AXUIElement) -> [AXUIElement] {
    var value: AnyObject?
    let result = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &value)
    guard result == .success, let children = value as? [AXUIElement] else { return [] }
    return children
}

func getFrame(_ element: AXUIElement) -> LegacyElement.Frame {
    let pos = getPosition(element)
    let sz = getSize(element)
    return LegacyElement.Frame(
        x: pos?.x ?? 0, y: pos?.y ?? 0,
        width: sz?.width ?? 0, height: sz?.height ?? 0
    )
}

// MARK: - AX Tree Walkers

/// Walk element tree — full AX attributes, tracking index path
func walkElementFull(_ element: AXUIElement, depth: Int = 0, maxDepth: Int = 15, currentPath: [Int] = []) -> AXExtractedElement? {
    guard depth < maxDepth else { return nil }

    let role = getStringAttribute(element, kAXRoleAttribute) ?? "AXUnknown"
    let subrole = getStringAttribute(element, kAXSubroleAttribute)
    let title = getStringAttribute(element, kAXTitleAttribute)
    let descriptionAttr = getStringAttribute(element, kAXDescriptionAttribute)
    let identifier = getStringAttribute(element, "AXIdentifier")
    let value = getStringAttribute(element, kAXValueAttribute as String)
    let enabled = getBoolAttribute(element, kAXEnabledAttribute)
    let focused = getBoolAttribute(element, kAXFocusedAttribute)
    let position = getPosition(element)
    let size = getSize(element)
    let actions = getActions(element)

    if let sz = size, sz.width <= 0 && sz.height <= 0 && depth > 2 {
        return nil
    }

    let axChildren = getChildren(element)
    var childElements: [AXExtractedElement] = []
    for (index, child) in axChildren.enumerated() {
        let childPath = currentPath + [index]
        if let childEl = walkElementFull(child, depth: depth + 1, maxDepth: maxDepth, currentPath: childPath) {
            childElements.append(childEl)
        }
    }

    return AXExtractedElement(
        role: role,
        subrole: subrole,
        title: title,
        description: descriptionAttr,
        identifier: identifier,
        value: value,
        enabled: enabled,
        focused: focused,
        actions: actions,
        position: position,
        size: size,
        children: childElements,
        path: currentPath
    )
}

/// Walk element tree — legacy format for simulator compatibility
func walkElementLegacy(_ element: AXUIElement, depth: Int = 0, maxDepth: Int = 15, currentPath: [Int] = []) -> LegacyElement? {
    guard depth < maxDepth else { return nil }

    let role = getStringAttribute(element, kAXRoleAttribute) ?? "AXUnknown"
    let label = getStringAttribute(element, kAXTitleAttribute)
        ?? getStringAttribute(element, kAXDescriptionAttribute)
        ?? getStringAttribute(element, kAXValueAttribute as String)
        ?? ""
    let identifier = getStringAttribute(element, "AXIdentifier") ?? ""
    let isEnabled = getBoolAttribute(element, kAXEnabledAttribute)
    let value = getStringAttribute(element, kAXValueAttribute as String)
    let frame = getFrame(element)

    if frame.width <= 0 && frame.height <= 0 && depth > 2 {
        return nil
    }

    let axChildren = getChildren(element)
    var childElements: [LegacyElement] = []
    for (index, child) in axChildren.enumerated() {
        let childPath = currentPath + [index]
        if let childEl = walkElementLegacy(child, depth: depth + 1, maxDepth: maxDepth, currentPath: childPath) {
            childElements.append(childEl)
        }
    }

    var traits: [String] = []
    if role == "AXButton" { traits.append("button") }
    if role == "AXLink" { traits.append("link") }
    if role == "AXStaticText" { traits.append("staticText") }
    if role == "AXImage" { traits.append("image") }
    if !isEnabled { traits.append("disabled") }

    return LegacyElement(
        identifier: identifier,
        label: label,
        role: role,
        traits: traits,
        frame: frame,
        isEnabled: isEnabled,
        value: value,
        path: currentPath,
        children: childElements
    )
}

// MARK: - App Finders

func findSimulatorApp() -> NSRunningApplication? {
    let apps = NSWorkspace.shared.runningApplications
    return apps.first { $0.bundleIdentifier == "com.apple.iphonesimulator" }
}

func findSimulatorWindow(app: NSRunningApplication, deviceName: String?) -> AXUIElement? {
    let appElement = AXUIElementCreateApplication(app.processIdentifier)

    var windowsValue: AnyObject?
    let result = AXUIElementCopyAttributeValue(appElement, kAXWindowsAttribute as CFString, &windowsValue)
    guard result == .success, let windows = windowsValue as? [AXUIElement] else { return nil }

    if let deviceName = deviceName {
        for window in windows {
            let title = getStringAttribute(window, kAXTitleAttribute) ?? ""
            if title.localizedCaseInsensitiveContains(deviceName) {
                return window
            }
        }
    }

    return windows.first
}

/// Find a running app by name (case-insensitive substring match)
func findAppByName(_ name: String) -> NSRunningApplication? {
    let apps = NSWorkspace.shared.runningApplications
    let lowered = name.lowercased()
    return apps.first { app in
        let appName = app.localizedName?.lowercased() ?? ""
        let bundleId = app.bundleIdentifier?.lowercased() ?? ""
        return appName.contains(lowered) || bundleId.contains(lowered)
    }
}

func findAppByPid(_ pid: pid_t) -> NSRunningApplication? {
    return NSWorkspace.shared.runningApplications.first { $0.processIdentifier == pid }
}

/// Find the frontmost/main window for a given app
func findMainWindow(pid: pid_t) -> (window: AXUIElement, id: CGWindowID, title: String, size: CGSize)? {
    let appElement = AXUIElementCreateApplication(pid)

    var windowsValue: AnyObject?
    let result = AXUIElementCopyAttributeValue(appElement, kAXWindowsAttribute as CFString, &windowsValue)
    guard result == .success, let windows = windowsValue as? [AXUIElement], !windows.isEmpty else {
        return nil
    }

    let window: AXUIElement
    var mainWindow: AnyObject?
    let mainResult = AXUIElementCopyAttributeValue(appElement, kAXMainWindowAttribute as CFString, &mainWindow)
    if mainResult == .success, let mw = mainWindow {
        window = (mw as! AXUIElement)
    } else {
        window = windows[0]
    }

    let title = getStringAttribute(window, kAXTitleAttribute) ?? "Untitled"
    let sz = getSize(window) ?? AXExtractedElement.Size(width: 800, height: 600)

    var windowId: CGWindowID = 0
    let windowList = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    for info in windowList {
        guard let ownerPid = info[kCGWindowOwnerPID as String] as? Int32,
              let wid = info[kCGWindowNumber as String] as? CGWindowID,
              ownerPid == pid else { continue }
        let wName = info[kCGWindowName as String] as? String ?? ""
        if wName == title || windowId == 0 {
            windowId = wid
            if wName == title { break }
        }
    }

    return (window: window, id: windowId, title: title,
            size: CGSize(width: sz.width, height: sz.height))
}

// MARK: - Action Execution

/// Traverse the AX tree by numeric index path from the app root
func navigateToElement(root: AXUIElement, path: [Int]) -> AXUIElement? {
    var current = root
    for index in path {
        var childrenRef: CFTypeRef?
        guard AXUIElementCopyAttributeValue(current, kAXChildrenAttribute as CFString, &childrenRef) == .success,
              let children = childrenRef as? [AXUIElement],
              index < children.count else { return nil }
        current = children[index]
    }
    return current
}

/// Execute an accessibility action on the element at the given path
func performAction(pid: pid_t, deviceName: String?, elementPath: [Int], action: String, value: String?) -> (Bool, String?) {
    let root: AXUIElement?
    if let app = findAppByPid(pid),
       app.bundleIdentifier == "com.apple.iphonesimulator",
       let simulatorWindow = findSimulatorWindow(app: app, deviceName: deviceName) {
        root = simulatorWindow
    } else {
        root = findMainWindow(pid: pid)?.window
    }

    guard let actionRoot = root else {
        return (false, "No action root window found for pid \(pid)")
    }

    guard let element = navigateToElement(root: actionRoot, path: elementPath) else {
        return (false, "Element not found at path")
    }

    switch action {
    case "press":
        let ok = AXUIElementPerformAction(element, kAXPressAction as CFString) == .success
        return (ok, ok ? nil : "AXPress failed")
    case "setValue":
        guard let value = value else {
            return (false, "setValue requires --value")
        }
        let ok = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, value as CFTypeRef) == .success
        return (ok, ok ? nil : "AXSetValue failed")
    case "increment":
        let ok = AXUIElementPerformAction(element, kAXIncrementAction as CFString) == .success
        return (ok, ok ? nil : "AXIncrement failed")
    case "decrement":
        let ok = AXUIElementPerformAction(element, kAXDecrementAction as CFString) == .success
        return (ok, ok ? nil : "AXDecrement failed")
    case "showMenu":
        let ok = AXUIElementPerformAction(element, kAXShowMenuAction as CFString) == .success
        return (ok, ok ? nil : "AXShowMenu failed")
    case "confirm":
        let ok = AXUIElementPerformAction(element, kAXConfirmAction as CFString) == .success
        return (ok, ok ? nil : "AXConfirm failed")
    case "cancel":
        let ok = AXUIElementPerformAction(element, kAXCancelAction as CFString) == .success
        return (ok, ok ? nil : "AXCancel failed")
    case "focus":
        let ok = AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, true as CFTypeRef) == .success
        return (ok, ok ? nil : "AXFocus failed")
    case "scrollToVisible":
        let ok = AXUIElementPerformAction(element, "AXScrollToVisible" as CFString) == .success
        return (ok, ok ? nil : "AXScrollToVisible failed")
    default:
        return (false, "Unknown action: \(action)")
    }
}

// MARK: - Main

let args = CommandLine.arguments

var deviceName: String? = nil
var targetPid: pid_t? = nil
var targetApp: String? = nil
var resolveApp: String? = nil
var outputMode: String = "auto"
var actionName: String? = nil
var elementPathStr: String? = nil
var actionValue: String? = nil

var i = 1
while i < args.count {
    switch args[i] {
    case "--device-name":
        i += 1
        if i < args.count { deviceName = args[i] }
    case "--pid":
        i += 1
        if i < args.count { targetPid = pid_t(args[i]) }
    case "--app":
        i += 1
        if i < args.count { targetApp = args[i] }
    case "--resolve-app":
        i += 1
        if i < args.count { resolveApp = args[i] }
    case "--format":
        i += 1
        if i < args.count { outputMode = args[i] }
    case "--action":
        i += 1
        if i < args.count { actionName = args[i] }
    case "--element-path":
        i += 1
        if i < args.count { elementPathStr = args[i] }  // comma-separated: "0,2,1"
    case "--value":
        i += 1
        if i < args.count { actionValue = args[i] }
    default:
        break
    }
    i += 1
}

// --- Mode: Resolve a running macOS app without AX permission ---
if let appName = resolveApp {
    guard let app = findAppByName(appName) else {
        fputs("Error: No running app found matching \"\(appName)\"\n", stderr)
        exit(1)
    }

    let payload: [String: Any] = [
        "pid": Int(app.processIdentifier),
        "name": app.localizedName ?? "",
        "bundleIdentifier": app.bundleIdentifier ?? ""
    ]
    if let data = try? JSONSerialization.data(withJSONObject: payload),
       let str = String(data: data, encoding: .utf8) {
        print(str)
        exit(0)
    }
    fputs("Error: Failed to encode app resolution JSON\n", stderr)
    exit(1)
}

// Check accessibility permission
let checkOpts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue(): true] as CFDictionary
guard AXIsProcessTrustedWithOptions(checkOpts) else {
    fputs("Error: Accessibility permission required. Grant access in System Settings > Privacy & Security > Accessibility\n", stderr)
    exit(1)
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]

// --- Mode: Action execution (--action --pid --element-path) ---
if let actionName = actionName, let pathStr = elementPathStr {
    guard let pid = targetPid else {
        fputs("{\"error\":\"--pid is required for action mode\"}\n", stderr)
        exit(1)
    }
    let path = pathStr.split(separator: ",").compactMap { Int($0) }
    let (success, error) = performAction(pid: pid, deviceName: deviceName, elementPath: path, action: actionName, value: actionValue)
    var result: [String: Any] = ["success": success, "action": actionName]
    if let error = error {
        result["error"] = error
    }
    if let data = try? JSONSerialization.data(withJSONObject: result),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    }
    exit(success ? 0 : 1)
}

// --- Mode 1: macOS app scanning (--pid or --app) ---
if targetPid != nil || targetApp != nil {
    let pid: pid_t
    if let p = targetPid {
        pid = p
    } else if let appName = targetApp {
        guard let app = findAppByName(appName) else {
            fputs("Error: No running app found matching \"\(appName)\"\n", stderr)
            fputs("Running apps:\n", stderr)
            for app in NSWorkspace.shared.runningApplications where app.activationPolicy == .regular {
                let name = app.localizedName ?? "Unknown"
                let bundle = app.bundleIdentifier ?? "unknown"
                fputs("  \(name) (\(bundle)) pid=\(app.processIdentifier)\n", stderr)
            }
            exit(1)
        }
        pid = app.processIdentifier
    } else {
        fputs("Error: No target specified\n", stderr)
        exit(1)
    }

    guard let windowInfo = findMainWindow(pid: pid) else {
        fputs("Error: No windows found for pid \(pid)\n", stderr)
        exit(1)
    }

    let widthInt = Int(windowInfo.size.width)
    let heightInt = Int(windowInfo.size.height)
    print("WINDOW:\(windowInfo.id):\(widthInt)x\(heightInt):\(windowInfo.title)")

    guard let rootElement = walkElementFull(windowInfo.window, currentPath: []) else {
        fputs("Error: Failed to walk accessibility tree\n", stderr)
        exit(1)
    }

    let output = rootElement.children
    let data = try encoder.encode(output)
    if let json = String(data: data, encoding: .utf8) {
        print(json)
    } else {
        fputs("Error: Failed to encode JSON\n", stderr)
        exit(1)
    }

    exit(0)
}

// --- Mode 2: Simulator scanning (--device-name or default) ---
guard let simApp = findSimulatorApp() else {
    fputs("Error: Simulator.app is not running\n", stderr)
    exit(1)
}

guard let window = findSimulatorWindow(app: simApp, deviceName: deviceName) else {
    let msg = deviceName != nil
        ? "Error: No simulator window found for device: \(deviceName!)"
        : "Error: No simulator windows found"
    fputs("\(msg)\n", stderr)
    exit(1)
}

guard let rootElement = walkElementLegacy(window, currentPath: []) else {
    fputs("Error: Failed to walk accessibility tree\n", stderr)
    exit(1)
}

let output = rootElement.children
let data = try encoder.encode(output)

if let json = String(data: data, encoding: .utf8) {
    print(json)
} else {
    fputs("Error: Failed to encode JSON\n", stderr)
    exit(1)
}
