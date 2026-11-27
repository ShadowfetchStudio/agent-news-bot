#!/bin/bash
# Build Agent News Bot locally. Installation is explicit: ./build.sh --install.
set -euo pipefail

INSTALL=0
ARCH="$(uname -m)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install) INSTALL=1; shift ;;
    --arch)
      ARCH="${2:-}"
      shift 2 ;;
    --help|-h)
      echo "Usage: ./build.sh [--install] [--arch arm64|x86_64|universal]"
      echo "Builds under build/. --install copies to /Applications only if absent."
      echo "Existing applications and NewsBot data are never removed."
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$ARCH" in
  arm64|x86_64|universal) ;;
  *) echo "Unsupported --arch: $ARCH (expected arm64, x86_64, or universal)" >&2; exit 2 ;;
esac

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$PROJECT_DIR/Sources"
RES="$PROJECT_DIR/Resources"
BUILD="$PROJECT_DIR/build"
APP_NAME="Agent News Bot"
BIN_NAME="AgentNewsBot"
APP_DIR="$BUILD/$APP_NAME.app"
INSTALL_PATH="/Applications/$APP_NAME.app"
BUNDLE_ID="ai.shadowfetch.agentnewsbot"

if [[ "$INSTALL" == 1 && -e "$INSTALL_PATH" ]]; then
  echo "Refusing to overwrite $INSTALL_PATH. Build without --install, then replace it manually after review." >&2
  exit 1
fi

cd "$PROJECT_DIR"
echo "==> Cleaning"
rm -rf "$BUILD"
mkdir -p "$BUILD" "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

echo "==> Icon"
swift "$RES/draw_icon.swift" "$BUILD/icon-1024.png"
ICONSET="$BUILD/AppIcon.iconset"
mkdir -p "$ICONSET"
PNG="$BUILD/icon-1024.png"
sips -z 16 16 "$PNG" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 "$PNG" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$PNG" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 "$PNG" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$PNG" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 "$PNG" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$PNG" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 "$PNG" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$PNG" --out "$ICONSET/icon_512x512.png" >/dev/null
cp "$PNG" "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o "$APP_DIR/Contents/Resources/AppIcon.icns"

cp "$PROJECT_DIR/linux/agent_newsbot/vendor/twitter-text.js" "$APP_DIR/Contents/Resources/"
cp "$PROJECT_DIR/linux/agent_newsbot/vendor/"*-LICENSE.txt "$APP_DIR/Contents/Resources/"
cp "$PROJECT_DIR/linux/agent_newsbot/recovery.py" "$APP_DIR/Contents/Resources/"
cp "$PROJECT_DIR/docs/RESEARCH_RECOVERY.md" "$APP_DIR/Contents/Resources/"

echo "==> Compiling ($ARCH)"
SWIFT_SOURCES=()
while IFS= read -r -d '' source; do
  SWIFT_SOURCES+=("$source")
done < <(find "$SRC" -name '*.swift' -print0)

compile_slice() {
  local target_arch="$1"
  local out_bin="$2"
  swiftc \
    -O \
    -whole-module-optimization \
    -target "${target_arch}-apple-macos14.0" \
    -parse-as-library \
    -framework SwiftUI \
    -framework AppKit \
    -framework Foundation \
    -framework Combine \
    -framework CryptoKit \
    -framework Security \
    -framework Network \
    -lsqlite3 \
    -o "$out_bin" \
    "${SWIFT_SOURCES[@]}"
}

if [[ "$ARCH" == "universal" ]]; then
  compile_slice arm64 "$BUILD/${BIN_NAME}-arm64"
  compile_slice x86_64 "$BUILD/${BIN_NAME}-x86_64"
  lipo -create "$BUILD/${BIN_NAME}-arm64" "$BUILD/${BIN_NAME}-x86_64" -output "$APP_DIR/Contents/MacOS/$BIN_NAME"
else
  compile_slice "$ARCH" "$APP_DIR/Contents/MacOS/$BIN_NAME"
fi
chmod +x "$APP_DIR/Contents/MacOS/$BIN_NAME"

echo "==> Info.plist"
cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Agent News Bot</string>
    <key>CFBundleDisplayName</key><string>Agent News Bot</string>
    <key>CFBundleExecutable</key><string>$BIN_NAME</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.2.0</string>
    <key>CFBundleVersion</key><string>2</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.news</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
    <key>NSSupportsAutomaticTermination</key><false/>
    <key>LSUIElement</key><false/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key><true/>
    </dict>
</dict>
</plist>
EOF

echo "==> Ad-hoc sign"
codesign --force --deep --sign - "$APP_DIR"
codesign --verify --deep --strict "$APP_DIR"

if [[ "$INSTALL" == 1 ]]; then
  if [[ -e "$INSTALL_PATH" ]]; then
    echo "Refusing to overwrite $INSTALL_PATH." >&2
    exit 1
  fi
  cp -R "$APP_DIR" "$INSTALL_PATH"
  echo "Installed: $INSTALL_PATH"
else
  echo "Built: $APP_DIR"
  echo "To install a new copy: ./build.sh --install"
fi
echo "Existing NewsBot applications and saved data were preserved."
