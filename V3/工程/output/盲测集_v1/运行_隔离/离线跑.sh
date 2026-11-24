#!/bin/bash
# 离线运行（无网络命名空间＋清掉百度环境变量），保证 0 次百度调用。用法：bash 离线跑.sh 脚本.py 参数...
# 【仓库副本：只改路径】工作目录原为试验机 baidu 根，现为仓库根；解释器优先用 $PY，其次原试验机 venv，最后 python3。
# 保留 OCR_BAIDU_ROOT（只是目录路径，不是密钥），其余名字含 baidu 的变量（含密钥）一律清掉。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../../../../环境.sh"
cd "$REPO_ROOT"
if [[ -z "${PY:-}" ]]; then
  if [[ -n "${OCR_BAIDU_ROOT:-}" && -x "$OCR_BAIDU_ROOT/.venv/bin/python" ]]; then PY="$OCR_BAIDU_ROOT/.venv/bin/python"; else PY="$(command -v python3)"; fi
fi
for v in $(env | cut -d= -f1 | grep -i baidu | grep -v '^OCR_BAIDU_ROOT$' || true); do unset "$v"; done
exec unshare -rn env PYTHONDONTWRITEBYTECODE=1 "$PY" "$@"
