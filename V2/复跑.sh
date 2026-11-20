#!/bin/bash
# 候选路线第三版（博凯清零）：一条命令复跑八套／博凯／江苏并计分。只用缓存；在无网络命名空间里运行，并去掉所有百度相关环境变量，保证 0 次调用。
# 用法：bash 复跑.sh            （全部数据集）
#       bash 复跑.sh 博凯 江苏  （只跑部分）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd /workspace/pdf-test/baidu
for v in $(env | cut -d= -f1 | grep -i baidu || true); do unset "$v"; done
exec unshare -rn env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python "$HERE/复跑_第三版.py" "$@"
