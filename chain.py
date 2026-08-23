#!/usr/bin/env python3
"""Split an email body into the individual messages of its chain.

A .eml is almost never one message. It is the message you were sent plus every
message it quotes, and when a chronology cites "the email of 15 December" the
thing worth pointing at is one message inside that pile, not the file.

Two shapes turn up in practice:

  * Outlook — a divider div (``border-top:solid``) followed by a header block
    reading ``From: … Sent: … To: … Subject:``. A survey of 58 real messages in
    one folder found this shape in every chained message bar one.
  * Gmail and Apple Mail — nested ``<blockquote>``.

Detection works on the *extracted text* of the header block rather than on the
markup around it, because the markup differs by client and by Outlook version
while the four field names do not. ``Date:`` is accepted alongside ``Sent:`` for
the same reason.

Segments are wrapped in ``<div class="eml-msg">`` without disturbing the
surrounding nesting: at each boundary every open tag is closed, the wrapper is
opened, and the same tags are reopened inside it.

Python 3.9 — the Swift wrapper invokes /usr/bin/python3, which is 3.9.6.
"""

import hashlib
import re
from html.parser import HTMLParser

# Tags that never have an end tag, so they must not go on the open-tag stack.
VOID = frozenset([
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
])

# "From:" opens a candidate; it is only a real boundary if "Subject:" and one of
# "Sent:"/"Date:" follow within this many characters of extracted text. Signature
# blocks and body prose that happen to contain "From:" fail that test.
HEADER_WINDOW = 400

# Two boundaries closer than this in extracted text are the same boundary.
# A real "From: … Sent: … To: … Subject:" block runs to 150-300 characters.
MIN_GAP = 60

_FIELD = r"(?:From|Sent|Date|To|Cc|Subject)"
_RE_SUBJECT = re.compile(r"\bSubject\s*:", re.I)
_RE_SENT = re.compile(r"\b(?:Sent|Date)\s*:", re.I)
_RE_FROM = re.compile(r"\bFrom\s*:", re.I)
# Field values run to the next field name or the end of the window.
_RE_VALUE = re.compile(
    r"\b%s\s*:\s*(.*?)(?=\s*\b%s\s*:|$)" % (_FIELD, _FIELD), re.I | re.S
)


def seg_key(text):
    """Stable 8-hex key for a segment, from its first 120 chars of text.

    Keyed on content rather than position so a link survives the same chain being
    re-rendered, and so quoting one more message underneath does not renumber
    everything above it.
    """
    norm = re.sub(r"\s+", " ", text or "").strip()[:120].lower()
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()[:8]


def _field(window, name):
    for m in re.finditer(_RE_VALUE, window):
        label = window[m.start():m.start() + 12].split(":")[0].strip().lower()
        if label == name:
            value = m.group(1)
            # Subject is the last field, so its match runs on into the body.
            # Every field ends at its line break; cut there before collapsing.
            value = value.split("\n")[0].split("\r")[0]
            return re.sub(r"\s+", " ", value).strip()[:200]
    return ""


# Apple Mail and Gmail introduce a quoted message with an attribution line
# instead of a header block: "On 1 Jan 2025, at 09:15, A Sender <a@example.com> wrote:".
# Real chains mix the two, because the people in them use different clients.
_RE_INTRO = re.compile(r"\bOn\s+(.{4,120}?)\s+wrote\s*:", re.I | re.S)


def _intro_fields(window):
    """Pull sender and date out of an 'On <date>, <person> wrote:' line."""
    m = _RE_INTRO.search(window)
    if not m:
        return "", ""
    inner = re.sub(r"\s+", " ", m.group(1)).strip()
    parts = [x.strip() for x in inner.split(",") if x.strip()]
    if len(parts) < 2:
        return inner[:200], ""
    # Trailing chunk names the person; everything before it is the timestamp.
    who = parts[-1]
    when = ", ".join(parts[:-1]).replace(" at ", " ")
    return who[:200], when[:200]


class _ChainSplitter(HTMLParser):
    """Re-emits HTML verbatim while recording where each quoted message begins."""

    def __init__(self):
        # convert_charrefs=False so entities are re-emitted as written; letting
        # the parser decode them would turn "&lt;" into a literal "<" on output
        # and corrupt every address in an Outlook header block.
        HTMLParser.__init__(self, convert_charrefs=False)
        self.out = []
        self.text = []          # extracted text, parallel to self.out
        self.text_len = 0
        self.stack = []         # [(tagname, raw start tag)]
        self.boundaries = []    # [{"out": i, "text": n, "stack": [...]}]
        self._pending_from = None
        self._pending_divider = None

    # -- helpers ---------------------------------------------------------
    def _snapshot(self):
        return {"out": len(self.out), "text": self.text_len,
                "stack": list(self.stack)}

    def _mark(self, snap):
        # Ignore a boundary that repeats one already recorded at the same place.
        if self.boundaries and self.boundaries[-1]["out"] == snap["out"]:
            return
        self.boundaries.append(snap)

    def _add_text(self, s):
        self.text.append(s)
        self.text_len += len(s)
        self._check_header()

    def _check_header(self):
        """Confirm a pending From: once Subject: and Sent:/Date: have followed."""
        if self._pending_from is None:
            return
        joined = "".join(self.text)
        window = joined[self._pending_from["text"]:
                        self._pending_from["text"] + HEADER_WINDOW]
        if _RE_SUBJECT.search(window) and _RE_SENT.search(window):
            # Prefer the divider that introduced this block, so the rule renders
            # inside the segment it belongs to rather than trailing the one above.
            snap = self._pending_from
            div = self._pending_divider
            if div is not None and 0 <= snap["out"] - div["out"] <= 6:
                snap = div
            self._mark(snap)
            self._pending_from = None
            self._pending_divider = None
        elif len(joined) - self._pending_from["text"] > HEADER_WINDOW:
            self._pending_from = None

    # -- parser callbacks ------------------------------------------------
    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text() or "<%s>" % tag
        if tag == "blockquote":
            self._mark(self._snapshot())
        else:
            for name, value in attrs:
                if name == "style" and value and "border-top:solid" in value:
                    self._pending_divider = self._snapshot()
                    break
        self.out.append(raw)
        if tag not in VOID:
            self.stack.append((tag, raw))

    def handle_startendtag(self, tag, attrs):
        self.out.append(self.get_starttag_text() or "<%s/>" % tag)

    def handle_endtag(self, tag):
        self.out.append("</%s>" % tag)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.out.append(data)
        if _RE_FROM.search(data) and self._pending_from is None:
            # Record the position of "From:" itself, not of the chunk holding it.
            offset = _RE_FROM.search(data).start()
            snap = self._snapshot()
            snap["text"] += offset
            self._pending_from = snap
        self._add_text(data)

    def handle_entityref(self, name):
        self.out.append("&%s;" % name)
        self._add_text(" ")

    def handle_charref(self, name):
        self.out.append("&#%s;" % name)
        self._add_text(" ")

    def handle_comment(self, data):
        self.out.append("<!--%s-->" % data)

    def handle_decl(self, decl):
        self.out.append("<!%s>" % decl)

    def unknown_decl(self, data):
        self.out.append("<![%s]>" % data)

    def handle_pi(self, data):
        self.out.append("<?%s>" % data)


def split_chain_html(html):
    """Wrap each message of an HTML chain. Returns (html, [segment, ...])."""
    p = _ChainSplitter()
    try:
        p.feed(html)
        p.close()
    except Exception:
        # Never let a malformed body cost the user the message itself.
        return html, []

    text = "".join(p.text)
    bounds = [b for b in p.boundaries if b["out"] > 0]

    # A single quoted message often trips two detectors at once: Gmail wraps it
    # in <blockquote> and then Outlook's header block appears immediately inside.
    # Genuine messages are separated by at least a header block's worth of text,
    # so anything closer than MIN_GAP is the same boundary seen twice.
    merged = []
    for b in bounds:
        if merged and b["text"] - merged[-1]["text"] < MIN_GAP:
            continue
        merged.append(b)
    bounds = merged

    if not bounds:
        return html, []

    # The top message is the run before the first boundary.
    bounds.insert(0, {"out": 0, "text": 0, "stack": []})

    segments = []
    for i, b in enumerate(bounds):
        end = bounds[i + 1]["text"] if i + 1 < len(bounds) else len(text)
        body = text[b["text"]:end]
        window = body[:HEADER_WINDOW]
        frm = _field(window, "from") if i else ""
        snt = (_field(window, "sent") or _field(window, "date")) if i else ""
        if i and not frm and not snt:
            frm, snt = _intro_fields(window)
        segments.append({
            "index": i,
            "key": seg_key(body),
            "from": frm,
            "sent": snt,
            "subject": _field(window, "subject") if i else "",
        })

    parts = []
    prev = 0
    for i, b in enumerate(bounds):
        parts.extend(p.out[prev:b["out"]])
        for tag, _raw in reversed(b["stack"]):
            parts.append("</%s>" % tag)
        if i:
            parts.append("</div>")
        s = segments[i]
        parts.append('<div class="eml-msg" id="msg-%d" data-key="%s">'
                     % (s["index"], s["key"]))
        for _tag, raw in b["stack"]:
            parts.append(raw)
        prev = b["out"]
    parts.extend(p.out[prev:])
    parts.append("</div>")
    return "".join(parts), segments


# Plain-text chains: "-----Original Message-----", an Outlook header block at the
# start of a line, or Gmail's "On <date>, X wrote:".
_RE_TEXT_BOUNDARY = re.compile(
    r"^(?:\s*-{2,}\s*Original Message\s*-{2,}\s*$"
    r"|\s*_{5,}\s*$"
    r"|\s*>*\s*From\s*:.*$"
    r"|\s*>*\s*On .{4,80}? wrote\s*:\s*$)",
    re.I | re.M,
)


def split_chain_text(text):
    """Split a plain-text chain. Returns [(segment_meta, chunk), ...]."""
    marks = [m.start() for m in _RE_TEXT_BOUNDARY.finditer(text)]
    marks = [m for m in marks if m > 0]
    if not marks:
        return []
    starts = [0] + marks
    out = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[s:e]
        window = chunk[:HEADER_WINDOW]
        out.append(({
            "index": i,
            "key": seg_key(chunk),
            "from": _field(window, "from") if i else "",
            "sent": (_field(window, "sent") or _field(window, "date")) if i else "",
            "subject": _field(window, "subject") if i else "",
        }, chunk))
    return out
