#!/bin/bash
# 在当前 Mac 上打出 .app、dmg 和 zip。Apple Silicon 与 Intel 都用本机架构。

set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
    echo "[ERROR] macOS 应用只能在 macOS 或 GitHub macOS Runner 上构建。" >&2
    exit 1
fi

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VERSION=$(awk '$1 == "version" && $2 == "=" { gsub(/"/, "", $3); print $3; exit }' "$ROOT_DIR/pyproject.toml")
MACHINE=$(uname -m)

case "$MACHINE" in
    arm64) ARCH_LABEL=apple-silicon ;;
    x86_64) ARCH_LABEL=intel ;;
    *)
        echo "[ERROR] 不支持的 macOS 架构: $MACHINE" >&2
        exit 1
        ;;
esac

if [ -z "$VERSION" ]; then
    echo "[ERROR] 无法从 pyproject.toml 读取版本号" >&2
    exit 1
fi

for tool in iconutil hdiutil codesign ditto; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "[ERROR] 未找到 macOS 构建工具: $tool" >&2
        exit 1
    fi
done

BUILD_DIR="$ROOT_DIR/build/macos-$ARCH_LABEL"
PACKAGE_DIR="$BUILD_DIR/package"
PYINSTALLER_DIST="$BUILD_DIR/pyinstaller-dist"
ICONSET="$BUILD_DIR/AppIcon.iconset"
ICON_FILE="$BUILD_DIR/Net Egress Switcher.icns"
APP_PATH="$ROOT_DIR/dist/网络出口切换.app"
DMG_PATH="$ROOT_DIR/dist/net-egress-switcher-macos-$ARCH_LABEL.dmg"
ZIP_PATH="$ROOT_DIR/dist/net-egress-switcher-macos-$ARCH_LABEL.zip"

rm -rf "$BUILD_DIR" "$APP_PATH"
rm -f "$DMG_PATH" "$ZIP_PATH" "$DMG_PATH.sha256" "$ZIP_PATH.sha256"
mkdir -p "$BUILD_DIR" "$ROOT_DIR/dist" "$ICONSET"

"$PYTHON_BIN" - <<PY
from pathlib import Path
from PIL import Image, ImageDraw
iconset = Path("$ICONSET")
sizes = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}
for name, size in sizes.items():
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = max(2, size // 5)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=(31, 111, 235, 255))
    y1 = int(size * 0.38)
    y2 = int(size * 0.62)
    draw.line((int(size * 0.22), y1, int(size * 0.62), y1), fill="white", width=max(2, size // 16))
    draw.line((int(size * 0.78), y2, int(size * 0.38), y2), fill="white", width=max(2, size // 16))
    image.save(iconset / name)
PY

iconutil -c icns "$ICONSET" -o "$ICON_FILE"

"$PYTHON_BIN" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "网络出口切换" \
    --icon "$ICON_FILE" \
    --osx-bundle-identifier "com.github.mi179.net-egress-switcher" \
    --paths "$ROOT_DIR/src" \
    --collect-submodules egress_switcher \
    --collect-all ttkbootstrap \
    --hidden-import tkinter \
    --hidden-import tkinter.ttk \
    --hidden-import PIL \
    --hidden-import pystray \
    --distpath "$PYINSTALLER_DIST" \
    --workpath "$BUILD_DIR/pyinstaller-work" \
    --specpath "$BUILD_DIR" \
    "$ROOT_DIR/gui_main.py"

ditto "$PYINSTALLER_DIST/网络出口切换.app" "$APP_PATH"

PLIST="$APP_PATH/Contents/Info.plist"
set_plist_string() {
    local key=$1
    local value=$2
    /usr/libexec/PlistBuddy -c "Set :$key $value" "$PLIST" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Add :$key string $value" "$PLIST"
}
set_plist_string CFBundleShortVersionString "$VERSION"
set_plist_string CFBundleVersion "$VERSION"
set_plist_string CFBundleDisplayName "网络出口切换"
set_plist_string LSMinimumSystemVersion "12.0"

codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"
plutil -lint "$PLIST"
test -x "$APP_PATH/Contents/MacOS/网络出口切换"

mkdir -p "$PACKAGE_DIR"
ditto "$APP_PATH" "$PACKAGE_DIR/网络出口切换.app"
ln -s /Applications "$PACKAGE_DIR/Applications"
install -m 0644 "$ROOT_DIR/docs/QUICK_START_MACOS.md" "$PACKAGE_DIR/使用说明.md"

hdiutil create \
    -volname "网络出口切换" \
    -srcfolder "$PACKAGE_DIR" \
    -format UDZO \
    -ov \
    "$DMG_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

(
    cd "$ROOT_DIR/dist"
    shasum -a 256 "$(basename "$DMG_PATH")" >"$(basename "$DMG_PATH").sha256"
    shasum -a 256 "$(basename "$ZIP_PATH")" >"$(basename "$ZIP_PATH").sha256"
)

echo "[OK] 网络出口切换 $VERSION ($ARCH_LABEL)"
echo "[OK] $DMG_PATH"
echo "[OK] $ZIP_PATH"
