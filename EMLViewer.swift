import Cocoa
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate {
    var windows: [NSWindow] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = CommandLine.arguments
        if args.count > 1 {
            openFile(args[1])
        }
    }

    func application(_ sender: NSApplication, openFile filename: String) -> Bool {
        openFile(filename)
        return true
    }

    func openFile(_ path: String) {
        // Use Python to parse the .eml and produce HTML
        let scriptDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
        // Try Dropbox-synced location first, then same directory as app
        var scriptPath = (scriptDir as NSString).appendingPathComponent("eml_viewer.py")
        if !FileManager.default.fileExists(atPath: scriptPath) {
            scriptPath = ((Bundle.main.resourcePath ?? scriptDir) as NSString).appendingPathComponent("eml_viewer.py")
        }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        task.arguments = [scriptPath, "--html-only", path]

        let pipe = Pipe()
        task.standardOutput = pipe

        do {
            try task.run()
            task.waitUntilExit()
        } catch {
            return
        }

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard let html = String(data: data, encoding: .utf8), !html.isEmpty else { return }

        // Extract title from HTML
        var title = (path as NSString).lastPathComponent
        if let range = html.range(of: "<title>"), let endRange = html.range(of: "</title>") {
            title = String(html[range.upperBound..<endRange.lowerBound])
        }

        let screenRect = NSScreen.main?.frame ?? NSMakeRect(0, 0, 800, 600)
        let w: CGFloat = 700
        let h: CGFloat = 800
        let x = (screenRect.width - w) / 2
        let y = (screenRect.height - h) / 2

        let window = NSWindow(
            contentRect: NSMakeRect(x, y, w, h),
            styleMask: [.titled, .closable, .resizable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = title

        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: NSMakeRect(0, 0, w, h), configuration: config)
        webView.loadHTMLString(html, baseURL: nil)

        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        windows.append(window)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
