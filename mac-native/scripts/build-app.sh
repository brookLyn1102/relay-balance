#!/bin/bash
# 编译并打包成可双击的 .app
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="中转管家"
BUNDLE_ID="com.relay.balance"
APP_VERSION="1.3.0"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
APP_DIR=".build/${NAME}.app"

echo "==> swift build -c release"
swift build -c release

BIN_DIR="$(swift build -c release --show-bin-path)"
BIN="${BIN_DIR}/RelayBalance"

if [ ! -f "$BIN" ]; then
    echo "没找到可执行文件：$BIN" >&2
    exit 1
fi

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BIN" "$APP_DIR/Contents/MacOS/RelayBalance"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>${NAME}</string>
  <key>CFBundleDisplayName</key><string>${NAME}</string>
  <key>CFBundleExecutable</key><string>RelayBalance</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${APP_VERSION}</string>
  <key>CFBundleVersion</key><string>${APP_VERSION}</string>
  <key>RelayCommit</key><string>${COMMIT}</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

echo "==> 打包完成：$APP_DIR"
echo "    双击打开：open \"$APP_DIR\""
echo "    开机自启：把 .app 拖进「系统设置 → 通用 → 登录项」"
