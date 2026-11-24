#!/bin/bash
# 离线运行（无网络命名空间＋清掉百度环境变量），保证 0 次百度调用。用法：bash 离线跑.sh 脚本.py 参数...
set -euo pipefail
cd /workspace/pdf-test/baidu
for v in $(env | cut -d= -f1 | grep -i baidu || true); do unset "$v"; done
exec unshare -rn env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python "$@"
