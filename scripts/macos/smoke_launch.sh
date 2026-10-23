#!/bin/bash
# 启动 .app 里的可执行文件，等几秒，确认进程还在，然后关掉。
# 用来抓住「后台线程调用 NSApplication.run()」这类一打开就崩溃的问题。

set -euo pipefail

BIN=${1:?需要 .app 里的可执行文件路径}
LOG=${2:-dist/macos-launch.log}
WAIT_SECONDS=${WAIT_SECONDS:-5}

mkdir -p "$(dirname "$LOG")"
: >"$LOG"

"$BIN" >>"$LOG" 2>&1 &
pid=$!

sleep "$WAIT_SECONDS"

if ! kill -0 "$pid" 2>/dev/null; then
    echo "应用启动后不到 ${WAIT_SECONDS} 秒就退出了（pid ${pid}）。" >&2
    cat "$LOG" >&2 || true
    report_dir="$HOME/Library/Logs/DiagnosticReports"
    if [ -d "$report_dir" ]; then
        newest=$(ls -t "$report_dir" 2>/dev/null | head -n 3 || true)
        if [ -n "$newest" ]; then
            echo "最近的崩溃报告：" >&2
            printf '%s\n' "$newest" >&2
        fi
    fi
    exit 1
fi

kill "$pid" 2>/dev/null || true
sleep 1
kill -9 "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
echo "smoke launch ok pid=${pid}"
