#!/bin/bash
# Build and install EML Viewer.
#
# The app is not in the repo (see .gitignore) and once went missing from disk
# entirely, taking the only built copy with it. This script is the recovery path:
# source plus this file is enough to get back to a working install.
#
# Installs to ~/Applications rather than /Applications to avoid colliding with
# the unrelated third-party app of the same name.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-$HOME/Applications/EML Viewer.app}"

# Built universal so one bundle runs on both the Apple Silicon machine and the
# Intel one at work. swiftc only emits the host arch, so each slice is compiled
# on its own and lipo'd together; the deployment target matches Info.plist.
echo "building..."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for ARCH in arm64 x86_64; do
    echo "  $ARCH"
    swiftc -O -target "$ARCH-apple-macos12.0" -o "$TMP/$ARCH" \
        "$SRC/EMLViewer.swift" -framework Cocoa -framework WebKit
done
lipo -create -output "$SRC/EMLViewerBin" "$TMP/arm64" "$TMP/x86_64"

echo "assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$SRC/EMLViewerBin" "$APP/Contents/MacOS/EML Viewer"
chmod +x "$APP/Contents/MacOS/EML Viewer"
# eml_viewer.py imports chain.py as a sibling, so both go in Resources. Keeping
# them inside the bundle stops the app being orphaned from its parser.
cp "$SRC/eml_viewer.py" "$SRC/chain.py" "$APP/Contents/Resources/"
cp "$SRC/Info.plist" "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null

echo "signing"
codesign --force --deep -s - "$APP"

echo "registering"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"

echo "done: $APP"
echo
echo "  open a message:  open -a \"$APP\" message.eml"
echo "  open at one msg: open 'emlviewer://open?file=<path>&msg=<key>'"
echo "  list chain keys: /usr/bin/python3 \"$APP/Contents/Resources/eml_viewer.py\" --list message.eml"
