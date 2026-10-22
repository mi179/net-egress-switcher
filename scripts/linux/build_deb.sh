#!/bin/bash
# 打一个架构无关的 Debian 包。界面主题是纯 Python，Pillow 用系统的 python3-pil。

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUTPUT_DIR=${1:-"$ROOT_DIR/dist"}
PACKAGE_NAME=net-egress-switcher
VERSION=$(awk '$1 == "version" && $2 == "=" { gsub(/"/, "", $3); print $3; exit }' "$ROOT_DIR/pyproject.toml")

if [ -z "$VERSION" ]; then
    echo "[ERROR] 无法从 pyproject.toml 读取版本号" >&2
    exit 1
fi
if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "[ERROR] 未找到 dpkg-deb，请先安装 dpkg-dev" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] 未找到 python3" >&2
    exit 1
fi

BUILD_DIR=$(mktemp -d)
PACKAGE_ROOT="$BUILD_DIR/$PACKAGE_NAME"
VENDOR="$BUILD_DIR/vendor"
trap 'rm -rf "$BUILD_DIR"' EXIT

python3 -m pip install --disable-pip-version-check --break-system-packages --target "$VENDOR" --upgrade --no-deps \
    "ttkbootstrap>=1.10.1,<2" \
    "pystray>=0.19"

install -d \
    "$PACKAGE_ROOT/DEBIAN" \
    "$PACKAGE_ROOT/usr/bin" \
    "$PACKAGE_ROOT/usr/lib/python3/dist-packages" \
    "$PACKAGE_ROOT/usr/share/applications" \
    "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps" \
    "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME"

cp -a "$ROOT_DIR/src/egress_switcher" "$PACKAGE_ROOT/usr/lib/python3/dist-packages/"
cp -a "$VENDOR/ttkbootstrap" "$PACKAGE_ROOT/usr/lib/python3/dist-packages/"
if [ -d "$VENDOR/pystray" ]; then
    cp -a "$VENDOR/pystray" "$PACKAGE_ROOT/usr/lib/python3/dist-packages/"
fi
find "$PACKAGE_ROOT" -depth -type d -name '__pycache__' -exec rm -rf {} +
find "$PACKAGE_ROOT" -type f -name '*.pyc' -delete

install -m 0755 /dev/stdin "$PACKAGE_ROOT/usr/bin/egress-switcher" <<'EOF'
#!/usr/bin/python3
from egress_switcher.cli import main

raise SystemExit(main())
EOF
ln -s egress-switcher "$PACKAGE_ROOT/usr/bin/egress-switcher-gui"
ln -s egress-switcher "$PACKAGE_ROOT/usr/bin/net-egress-switcher"

install -m 0644 /dev/stdin "$PACKAGE_ROOT/usr/share/applications/net-egress-switcher.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Net Egress Switcher
Name[zh_CN]=网络出口切换
Comment=Choose which network interface is the default IPv4 egress
Comment[zh_CN]=选择哪张网卡作为 IPv4 默认出口
Exec=egress-switcher-gui
Icon=net-egress-switcher
Terminal=false
Categories=Network;Settings;
Keywords=network;route;wifi;ethernet;egress;出口;
StartupNotify=true
EOF

install -m 0644 \
    "$ROOT_DIR/assets/net-egress-switcher.svg" \
    "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps/net-egress-switcher.svg"

install -m 0644 "$ROOT_DIR/LICENSE" "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/copyright"
install -m 0644 "$ROOT_DIR/README.md" "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/README.md"
install -m 0644 "$ROOT_DIR/docs/LINUX_USAGE.md" "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/LINUX_USAGE.md"
install -m 0644 "$ROOT_DIR/docs/HARDWARE_CHECKLIST.md" "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/HARDWARE_CHECKLIST.md"

BUILD_EPOCH=${SOURCE_DATE_EPOCH:-$(git -C "$ROOT_DIR" log -1 --format=%ct 2>/dev/null || date +%s)}
CHANGELOG_DATE=$(date --utc --date="@$BUILD_EPOCH" --rfc-email)
install -m 0644 /dev/stdin "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/changelog.Debian" <<EOF
$PACKAGE_NAME ($VERSION) unstable; urgency=medium

  * Build net-egress-switcher $VERSION.

 -- mi179 <noreply@github.com>  $CHANGELOG_DATE
EOF
gzip -9n "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/changelog.Debian"

INSTALLED_SIZE=$(du -sk "$PACKAGE_ROOT/usr" | cut -f1)
install -m 0644 /dev/stdin "$PACKAGE_ROOT/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Maintainer: mi179 <noreply@github.com>
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.10), python3-tk, python3-pil, iproute2, pkexec
Recommends: network-manager
Homepage: https://github.com/mi179/net-egress-switcher
Description: switch the default IPv4 network egress
 网络出口切换 lists wired, Wi-Fi and USB-tether interfaces and, after an
 explicit confirmation, makes one of them the preferred IPv4 default route.
 IPv6 is shown only. TUN and virtual adapters are labeled and left unchanged.
EOF

find "$PACKAGE_ROOT" -type d -exec chmod 0755 {} +
mkdir -p "$OUTPUT_DIR"
OUTPUT_FILE="$OUTPUT_DIR/${PACKAGE_NAME}_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$PACKAGE_ROOT" "$OUTPUT_FILE"
install -m 0644 "$OUTPUT_FILE" "$OUTPUT_DIR/${PACKAGE_NAME}_all.deb"
(
    cd "$OUTPUT_DIR"
    sha256sum "$(basename "$OUTPUT_FILE")" >"$(basename "$OUTPUT_FILE").sha256"
    sha256sum "${PACKAGE_NAME}_all.deb" >"${PACKAGE_NAME}_all.deb.sha256"
)
echo "[OK] $OUTPUT_FILE"
