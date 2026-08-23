#!/usr/bin/env python3
"""Open an .eml file in a native macOS window using WebKit."""

import email
import email.policy
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
import html as html_mod

from chain import split_chain_html, split_chain_text

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


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._ +()-]")


def attachment_dir(eml_path):
    """Stable scratch dir per message, so reopening the same file reuses extractions."""
    st = os.stat(eml_path)
    key = "%s|%s|%s" % (os.path.abspath(eml_path), st.st_mtime_ns, st.st_size)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), "eml-viewer", digest)
    os.makedirs(path, exist_ok=True)
    return path


def safe_name(filename, index, taken):
    """Strip anything that could escape the scratch dir, and de-duplicate."""
    name = _UNSAFE_NAME.sub("_", os.path.basename(filename or "").strip())
    if not name or name.startswith("."):
        name = "attachment-%d" % index
    stem, ext = os.path.splitext(name)
    candidate, n = name, 2
    while candidate in taken:
        candidate = "%s-%d%s" % (stem, n, ext)
        n += 1
    taken.add(candidate)
    return candidate


def human_size(n):
    if n < 1024:
        return "%d bytes" % n
    if n < 1024 * 1024:
        return "%.0f KB" % (n / 1024)
    return "%.1f MB" % (n / (1024 * 1024))


def _js_string(value):
    """JSON-encode for safe interpolation into a <script> block."""
    return json.dumps(value).replace("</", "<\\/")


def parse_eml(eml_path, anchor=None):
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
                payload = part.get_payload(decode=True) or b""
                attachments.append((part.get_filename(), content_type, payload))
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

    # Split the chain so each quoted message is separately addressable. A link
    # into a chronology usually means one message in the pile, not the file.
    segments = []
    body_html = ""
    if html_body:
        body_html, segments = split_chain_html(html_body)
    elif text_body:
        chunks = split_chain_text(text_body)
        if chunks:
            pieces = []
            for meta, chunk in chunks:
                pieces.append(
                    "<div class='eml-msg' id='msg-%d' data-key='%s'>"
                    "<pre style='white-space:pre-wrap;font-family:inherit;'>%s</pre></div>"
                    % (meta["index"], meta["key"], html_mod.escape(chunk))
                )
            body_html = "".join(pieces)
            segments = [m for m, _ in chunks]
        else:
            body_html = "<pre style='white-space:pre-wrap;font-family:inherit;'>" + html_mod.escape(text_body) + "</pre>"
    else:
        body_html = "<p style='color:#999;'>No displayable content</p>"

    attachments_html = ""
    if attachments:
        # Write each attachment out so the HTML can link to it; the Swift wrapper
        # intercepts the click and hands the file to Launch Services.
        out_dir = attachment_dir(eml_path)
        taken = set()
        parts = []
        for index, (filename, ctype, payload) in enumerate(attachments, 1):
            name = safe_name(filename, index, taken)
            dest = os.path.join(out_dir, name)
            if not os.path.exists(dest) or os.path.getsize(dest) != len(payload):
                with open(dest, "wb") as fh:
                    fh.write(payload)
            # Custom scheme, not file://: WebKit silently blocks file:// links
            # from an about:blank origin without ever consulting the
            # navigation delegate. The Swift wrapper unpacks this back to a path.
            href = "eml-attachment://" + urllib.parse.quote(dest)
            label = html_mod.escape(filename or name)
            parts.append(
                f"<li><a class='attachment' href='{html_mod.escape(href, quote=True)}'>{label}</a>"
                f" <span style='color:#999;'>({ctype}, {human_size(len(payload))})</span></li>"
            )
        items = "".join(parts)
        attachments_html = f"""
        <div style="margin-top:16px;padding:12px;background:#f8f8f8;border-radius:6px;">
            <strong>Attachments ({len(attachments)})</strong>
            <ul style="margin:8px 0 0 20px;">{items}</ul>
        </div>"""

    cc_line = f'<div style="margin-bottom:4px;"><strong>Cc:</strong> {html_mod.escape(cc_addr)}</div>' if cc_addr else ""

    # A chain of 20 messages is hard to navigate and harder to cite. List them,
    # newest first, and let a click flash the one you want.
    chain_html = ""
    if len(segments) > 1:
        rows = []
        for seg in segments:
            if seg["index"] == 0:
                who, when = "This message", ""
            else:
                who = seg["from"] or "(sender not identified)"
                when = seg["sent"]
            rows.append(
                "<li><a href='#' onclick=\"return emlHighlight('{key}')\">"
                "<span class='who'>{who}</span>"
                "<span class='when'>{when}</span></a></li>".format(
                    key=seg["key"],
                    who=html_mod.escape(who),
                    when=html_mod.escape(when),
                )
            )
        chain_html = (
            "<details class='chain'><summary>{n} messages in this chain</summary>"
            "<ol class='chain-list'>{rows}</ol></details>".format(
                n=len(segments), rows="".join(rows)
            )
        )

    # Injected only when a link asked for a specific message.
    anchor_js = ""
    if anchor:
        anchor_js = (
            "<script>window.addEventListener('load',function(){{"
            "emlHighlight({a});}});</script>".format(a=_js_string(anchor))
        )

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
    a.attachment {{ color: #0066cc; text-decoration: none; }}
    a.attachment:hover {{ text-decoration: underline; }}

    /* Chain navigator */
    .chain {{ margin-top: 12px; font-size: 12px; }}
    .chain summary {{ cursor: pointer; color: #555; user-select: none; }}
    .chain-list {{ margin: 8px 0 0 0; padding-left: 22px; color: #444; }}
    .chain-list li {{ margin: 3px 0; }}
    .chain-list a {{ color: #0066cc; text-decoration: none; display: flex; gap: 10px; }}
    .chain-list a:hover {{ text-decoration: underline; }}
    .chain-list .who {{ flex: 1 1 auto; }}
    .chain-list .when {{ flex: 0 0 auto; color: #999; }}

    /* A cited message is flashed, not permanently marked: three pulses, then a
       tint that fades. Anything sticky would look like the user's own highlight. */
    .eml-msg {{ scroll-margin-top: 14px; border-radius: 4px; }}
    @keyframes eml-flash {{
        0%, 100% {{ background-color: transparent; }}
        12%, 42%, 72% {{ background-color: rgba(255, 221, 51, 0.62); }}
        27%, 57%, 87% {{ background-color: transparent; }}
    }}
    .eml-msg.eml-flash {{ animation: eml-flash 1.8s ease-in-out 1; }}
    .eml-msg.eml-linger {{ background-color: rgba(255, 221, 51, 0.14); transition: background-color 2.5s ease; }}
    @media (prefers-reduced-motion: reduce) {{
        .eml-msg.eml-flash {{ animation: none; background-color: rgba(255, 221, 51, 0.45); }}
    }}
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
        {chain_html}
    </div>
    <div class="body">{body_html}</div>
<script>
function emlHighlight(key) {{
    var el = document.querySelector('[data-key="' + key + '"]')
          || document.getElementById('msg-' + key);
    if (!el) {{ return false; }}
    el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    el.classList.remove('eml-flash', 'eml-linger');
    void el.offsetWidth;                 // restart the animation if re-clicked
    el.classList.add('eml-flash');
    setTimeout(function () {{
        el.classList.remove('eml-flash');
        el.classList.add('eml-linger');
        setTimeout(function () {{ el.classList.remove('eml-linger'); }}, 2500);
    }}, 1800);
    return false;
}}
</script>
{anchor_js}
</body>
</html>"""

    return subject, page, segments


def show_native(eml_path, anchor=None):
    subject, html, _ = parse_eml(eml_path, anchor)

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


def show_browser(eml_path, anchor=None):
    import webbrowser
    _, html, _ = parse_eml(eml_path, anchor)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        tmp_path = f.name
    webbrowser.open("file://" + tmp_path)


def chain_of(eml_path):
    """The chain of an .eml as a list of segment dicts, newest first."""
    return parse_eml(eml_path)[2]


def _take_anchor():
    """Pull --anchor KEY (or --anchor=KEY) out of argv. Returns the key or None."""
    for i, arg in enumerate(list(sys.argv)):
        if arg == "--anchor" and i + 1 < len(sys.argv):
            key = sys.argv[i + 1]
            del sys.argv[i:i + 2]
            return key
        if arg.startswith("--anchor="):
            del sys.argv[i]
            return arg.split("=", 1)[1]
    return None


def main():
    # --anchor KEY scrolls to one message of the chain and flashes it.
    anchor = _take_anchor()

    # Handle --html-only flag: output HTML to stdout (used by Swift wrapper)
    if "--html-only" in sys.argv:
        sys.argv.remove("--html-only")
        if len(sys.argv) < 2:
            sys.exit(1)
        eml_path = sys.argv[1]
        if not os.path.exists(eml_path):
            sys.exit(1)
        _, html, _ = parse_eml(eml_path, anchor)
        sys.stdout.write(html)
        return

    # --list prints the chain so a link can be built without opening a window.
    if "--list" in sys.argv:
        sys.argv.remove("--list")
        if len(sys.argv) < 2 or not os.path.exists(sys.argv[1]):
            sys.exit(1)
        for seg in chain_of(sys.argv[1]):
            print("%s\t%s\t%s" % (
                seg["key"],
                seg["from"] or ("(this message)" if seg["index"] == 0 else "?"),
                seg["sent"],
            ))
        return

    if len(sys.argv) < 2:
        print("Usage: eml_viewer.py [--anchor KEY] [--list] <file.eml>")
        sys.exit(1)

    eml_path = sys.argv[1]
    if not os.path.exists(eml_path):
        print(f"File not found: {eml_path}")
        sys.exit(1)

    if HAS_WEBKIT and sys.platform == "darwin":
        show_native(eml_path, anchor)
    else:
        show_browser(eml_path, anchor)


if __name__ == "__main__":
    main()
