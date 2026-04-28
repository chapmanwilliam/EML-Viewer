#!/usr/bin/env python3
"""Open an .eml file in a native macOS window using WebKit."""

import email
import email.policy
import os
import sys
import html as html_mod

try:
    import objc
    from Foundation import NSObject, NSURL, NSApplication, NSApp, NSMakeRect
    from AppKit import (
        NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
        NSWindowStyleMaskResizable, NSWindowStyleMaskMiniaturizable,
        NSBackingStoreBuffered, NSScreen,
    )
    from WebKit import WKWebView, WKWebViewConfiguration
    HAS_WEBKIT = True
except ImportError:
    HAS_WEBKIT = False


def parse_eml(eml_path):
    with open(eml_path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)

    subject = msg.get("subject", "(no subject)")
    from_addr = msg.get("from", "")
    to_addr = msg.get("to", "")
    cc_addr = msg.get("cc", "")
    date = msg.get("date", "")

    html_body = None
    text_body = None
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                filename = part.get_filename() or "attachment"
                size = len(part.get_payload(decode=True) or b"")
                attachments.append((filename, content_type, size))
            elif content_type == "text/html" and html_body is None:
                html_body = part.get_content()
            elif content_type == "text/plain" and text_body is None:
                text_body = part.get_content()
    else:
        content_type = msg.get_content_type()
        if content_type == "text/html":
            html_body = msg.get_content()
        else:
            text_body = msg.get_content()

    body_html = ""
    if html_body:
        body_html = html_body
    elif text_body:
        body_html = "<pre style='white-space:pre-wrap;font-family:inherit;'>" + html_mod.escape(text_body) + "</pre>"
    else:
        body_html = "<p style='color:#999;'>No displayable content</p>"

    attachments_html = ""
    if attachments:
        items = "".join(
            f"<li>{html_mod.escape(name)} <span style='color:#999;'>({ctype}, {size//1024}KB)</span></li>"
            for name, ctype, size in attachments
        )
        attachments_html = f"""
        <div style="margin-top:16px;padding:12px;background:#f8f8f8;border-radius:6px;">
            <strong>Attachments ({len(attachments)})</strong>
            <ul style="margin:8px 0 0 20px;">{items}</ul>
        </div>"""

    cc_line = f'<div style="margin-bottom:4px;"><strong>Cc:</strong> {html_mod.escape(cc_addr)}</div>' if cc_addr else ""

    page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 0; background: #fff; }}
    .header {{ background: #fafafa; border-bottom: 1px solid #e0e0e0; padding: 16px 20px; }}
    .subject {{ font-size: 16px; font-weight: 600; margin-bottom: 10px; color: #1a1a1a; }}
    .meta {{ font-size: 12px; color: #555; line-height: 1.6; }}
    .meta strong {{ color: #333; }}
    .body {{ padding: 20px; font-size: 14px; line-height: 1.5; }}
</style>
</head>
<body>
    <div class="header">
        <div class="subject">{html_mod.escape(subject)}</div>
        <div class="meta">
            <div style="margin-bottom:3px;"><strong>From:</strong> {html_mod.escape(from_addr)}</div>
            <div style="margin-bottom:3px;"><strong>To:</strong> {html_mod.escape(to_addr)}</div>
            {cc_line}
            <div><strong>Date:</strong> {html_mod.escape(date)}</div>
        </div>
        {attachments_html}
    </div>
    <div class="body">{body_html}</div>
</body>
</html>"""

    return subject, page


def show_native(eml_path):
    subject, html = parse_eml(eml_path)

    app = NSApplication.sharedApplication()

    # Window size
    screen = NSScreen.mainScreen().frame()
    w, h = 700, 800
    x = (screen.size.width - w) / 2
    y = (screen.size.height - h) / 2
    rect = NSMakeRect(x, y, w, h)

    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
             NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, style, NSBackingStoreBuffered, False
    )
    window.setTitle_(subject)

    config = WKWebViewConfiguration.alloc().init()
    webview = WKWebView.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, w, h), config
    )
    webview.loadHTMLString_baseURL_(html, None)

    window.setContentView_(webview)
    window.makeKeyAndOrderFront_(None)

    # Keep a reference so window isn't garbage collected
    app._eml_window = window

    app.activateIgnoringOtherApps_(True)
    app.run()


def show_browser(eml_path):
    import tempfile
    import webbrowser
    _, html = parse_eml(eml_path)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        tmp_path = f.name
    webbrowser.open("file://" + tmp_path)


def main():
    # Handle --html-only flag: output HTML to stdout (used by Swift wrapper)
    if "--html-only" in sys.argv:
        sys.argv.remove("--html-only")
        if len(sys.argv) < 2:
            sys.exit(1)
        eml_path = sys.argv[1]
        if not os.path.exists(eml_path):
            sys.exit(1)
        _, html = parse_eml(eml_path)
        sys.stdout.write(html)
        return

    if len(sys.argv) < 2:
        print("Usage: eml_viewer.py <file.eml>")
        sys.exit(1)

    eml_path = sys.argv[1]
    if not os.path.exists(eml_path):
        print(f"File not found: {eml_path}")
        sys.exit(1)

    if HAS_WEBKIT and sys.platform == "darwin":
        show_native(eml_path)
    else:
        show_browser(eml_path)


if __name__ == "__main__":
    main()
