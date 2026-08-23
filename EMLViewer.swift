import Cocoa
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, NSWindowDelegate {
    var windows: [NSWindow] = []
    // Open messages by file path, so a second link to a file already on screen
    // can move the highlight instead of stacking another window. Drafting a
    // chronology means clicking many links in a row against the same few files.
    var openDocs: [String: (window: NSWindow, webView: WKWebView)] = [:]
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

        // A URL launch arrives through application(_:open:) instead, so argv
        // handling stays for the plain "open this file" case plus --anchor.
        var anchor: String? = nil
        var path: String? = nil
        var i = 1
        let args = CommandLine.arguments
        while i < args.count {
            let arg = args[i]
            if arg == "--anchor", i + 1 < args.count {
                anchor = args[i + 1]
                i += 2
            } else if arg.hasPrefix("--anchor=") {
                anchor = String(arg.dropFirst("--anchor=".count))
                i += 1
            } else {
                if path == nil { path = arg }
                i += 1
            }
        }
        if let p = path {
            openFile(p, anchor: anchor)
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

    // Links arrive as emlviewer://open?file=<path>&msg=<key>. A URL scheme rather
    // than an argv call because Word, Google Docs, WorkFlowy and Obsidian will all
    // make a URL clickable, and Launch Services delivers it whether or not the app
    // is already running.
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls {
            handle(url)
        }
    }

    func handle(_ url: URL) {
        guard let comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return }
        var file: String? = nil
        var msg: String? = nil
        for item in comps.queryItems ?? [] {
            switch item.name {
            case "file": file = item.value
            case "msg", "anchor": msg = item.value
            default: break
            }
        }
        guard let raw = file, let resolved = resolvePath(raw) else { return }
        openFile(resolved, anchor: msg)
    }

    // `file` may be absolute or relative to the Dropbox root, matching the form
    // pdfserver:// links already use. Resolving dbid stays pdfserver's job: it
    // owns the Dropbox mapping, and duplicating it here would give two answers.
    func resolvePath(_ raw: String) -> String? {
        let fm = FileManager.default
        if raw.hasPrefix("/"), fm.fileExists(atPath: raw) { return raw }
        if let root = dropboxRoot() {
            let joined = (root as NSString).appendingPathComponent(raw)
            if fm.fileExists(atPath: joined) { return joined }
        }
        return fm.fileExists(atPath: raw) ? raw : nil
    }

    func dropboxRoot() -> String? {
        let info = NSString(string: "~/.dropbox/info.json").expandingTildeInPath
        guard let data = FileManager.default.contents(atPath: info),
              let obj = try? JSONSerialization.jsonObject(with: data),
              let dict = obj as? [String: Any] else { return nil }
        for key in ["personal", "business"] {
            if let account = dict[key] as? [String: Any],
               let path = account["path"] as? String {
                return path
            }
        }
        return nil
    }

    func jsString(_ value: String) -> String {
        let escaped = value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        return "\"" + escaped + "\""
    }

    // Dropping the entry lets a later link reopen the file rather than trying to
    // talk to a WKWebView whose window has gone.
    func windowWillClose(_ notification: Notification) {
        guard let closing = notification.object as? NSWindow else { return }
        for (path, doc) in openDocs where doc.window === closing {
            openDocs.removeValue(forKey: path)
        }
        windows.removeAll { $0 === closing }
    }

    func application(_ sender: NSApplication, openFile filename: String) -> Bool {
        openFile(filename)
        return true
    }

    func openFile(_ path: String, anchor: String? = nil) {
        // Already on screen: move the highlight rather than opening a duplicate.
        if let doc = openDocs[path] {
            doc.window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            if let key = anchor {
                doc.webView.evaluateJavaScript("emlHighlight(\(jsString(key)));",
                                               completionHandler: nil)
            }
            return
        }

        // Use Python to parse the .eml and produce HTML
        let scriptDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
        // Try Dropbox-synced location first, then same directory as app
        var scriptPath = (scriptDir as NSString).appendingPathComponent("eml_viewer.py")
        if !FileManager.default.fileExists(atPath: scriptPath) {
            scriptPath = ((Bundle.main.resourcePath ?? scriptDir) as NSString).appendingPathComponent("eml_viewer.py")
        }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        // Passing the anchor to Python rather than evaluating JS after load means
        // the highlight is baked into the page and cannot race the navigation.
        if let key = anchor {
            task.arguments = [scriptPath, "--html-only", "--anchor", key, path]
        } else {
            task.arguments = [scriptPath, "--html-only", path]
        }

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
        window.delegate = self
        window.makeKeyAndOrderFront(nil)
        windows.append(window)
        openDocs[path] = (window: window, webView: webView)
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
