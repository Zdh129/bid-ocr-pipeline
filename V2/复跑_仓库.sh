#!/bin/bash
# 在本仓库内做 V2 依赖导入自测。不改已有的 复跑.sh（仍指向原试验机路径）。
# 完整离线复跑需要本地识别缓存，本仓库不提供数据集或缓存。
# 用法：bash 复跑_仓库.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/环境.sh"
PY="${PYTHON:-python3}"
"$PY" "$HERE/自测_导入.py"
echo "导入自测已通过。完整复跑请在有本地缓存的试验环境中使用原 复跑.sh（unshare -rn，0 次百度调用）。"
