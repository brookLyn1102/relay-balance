#!/bin/bash
# 编译并打包成可双击的 .app
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="中转管家"
BUNDLE_ID="com.relay.balance"
APP_VERSION="1.4.0"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
APP_DIR=".build/${NAME}.app"

# SwiftPM 编译 Package.swift 的 manifest 时会自己套一层 sandbox-exec。
# 在拿不到完整沙箱权限的环境里（CI 容器、受限 shell、被外层沙箱包住的
# Agent 工具）这一步会直接失败：
#   sandbox-exec: sandbox_apply: Operation not permitted
# 普通终端和 Xcode 环境下不会走到这条路，所以先按默认编译，确认是这条
# 报错再退到 --disable-sandbox 重试一次。
build_release() {
    local log
    log="$(mktemp -t relay-balance-build)"
    if swift build -c release >"$log" 2>&1; then
        tail -n 3 "$log"
        rm -f "$log"
        return 0
    fi
    if grep -q "sandbox_apply: Operation not permitted" "$log"; then
        echo "!! SwiftPM 沙箱不可用，改用 --disable-sandbox 重试（产物相同）" >&2
        rm -f "$log"
        swift build -c release --disable-sandbox
        return $?
    fi
    cat "$log" >&2
    rm -f "$log"
    return 1
}

echo "==> swift build -c release"
build_release

# --show-bin-path 同样要加载 manifest，受限环境下也会挂，挂就退回默认目录。
BIN_DIR="$(swift build -c release --show-bin-path 2>/dev/null || echo ".build/release")"
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
echo "    开机自启：应用内「设置 → 启动 → 开机自启」（需 macOS 13+）"
