import Cocoa
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var windows: [NSWindow] = []
    var keyMonitor: Any?

    // Built in code because the app has no nib. Without a main menu the standard
    // shortcuts have nothing to hang off: no Cmd-Q, and no Edit menu means the
    // WKWebView never receives copy:/selectAll: for Cmd-C and Cmd-A.
    func buildMenu() {
        let appName = (Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String)
            ?? ProcessInfo.processInfo.processName
        let mainMenu = NSMenu()

        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let appMenu = NSMenu()
        appItem.submenu = appMenu
        appMenu.addItem(withTitle: "About \(appName)",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
                        keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide \(appName)",
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "Hide Others",
                                         action: #selector(NSApplication.hideOtherApplications(_:)),
                                         keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All",
                        action: #selector(NSApplication.unhideAllApplications(_:)),
                        keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(appName)",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let fileItem = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        mainMenu.addItem(fileItem)
        let fileMenu = NSMenu(title: "File")
        fileItem.submenu = fileMenu
        fileMenu.addItem(withTitle: "Close",
                         action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editItem.submenu = editMenu
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Select All",
                         action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        let windowItem = NSMenuItem(title: "Window", action: nil, keyEquivalent: "")
        mainMenu.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window")
        windowItem.submenu = windowMenu
        windowMenu.addItem(withTitle: "Minimize",
                           action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom",
                           action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")

        NSApp.mainMenu = mainMenu
        NSApp.windowsMenu = windowMenu
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()

        // Escape and Cmd-W close the front window. A local monitor because the
        // WKWebView swallows Escape before it reaches the responder chain.
        // Cmd-W is also File > Close; handling it here too means it still works
        // when there is no key window for performClose: to be routed to.
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            let isEscape = event.keyCode == 53
            let isCmdW = event.modifierFlags.contains(.command)
                && event.charactersIgnoringModifiers?.lowercased() == "w"
            guard isEscape || isCmdW else { return event }
            // keyWindow can be nil if the app isn't frontmost, so fall back to
            // the most recently opened message window.
            let target = NSApp.keyWindow
                ?? NSApp.mainWindow
                ?? self?.windows.last(where: { $0.isVisible })
            target?.performClose(nil)
            return nil
        }

        let args = CommandLine.arguments
        if args.count > 1 {
            openFile(args[1])
        }
    }

    // Attachments (and any link in the message body) are handed to Launch
    // Services rather than loaded into the message window.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.navigationType == .linkActivated,
           let url = navigationAction.request.url {
            if url.scheme == "eml-attachment" {
                NSWorkspace.shared.open(URL(fileURLWithPath: url.path))
            } else {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
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
        } catch {
            return
        }

        // Read pipe to EOF before waitUntilExit: stdout >16KB will block the
        // child if no one drains it, deadlocking against waitUntilExit.
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
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
        // `windows` holds the strong reference, so don't let AppKit free it on close.
        window.isReleasedWhenClosed = false

        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: NSMakeRect(0, 0, w, h), configuration: config)
        webView.navigationDelegate = self
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
