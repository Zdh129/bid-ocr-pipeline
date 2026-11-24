#!/bin/bash
# 候选路线第三版：一条命令复跑并计分。只用缓存；在无网络命名空间里运行，并去掉所有百度相关环境变量，保证 0 次调用。
# 用法：bash 复跑.sh            （全部数据集）
#       bash 复跑.sh 测试样本A 江苏  （只跑部分）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/环境.sh"
PY="${PY:-}"
if [[ -z "$PY" ]]; then
  if [[ -n "${OCR_BAIDU_ROOT:-}" && -x "${OCR_BAIDU_ROOT}/.venv/bin/python" ]]; then
    PY="${OCR_BAIDU_ROOT}/.venv/bin/python"
  elif [[ -x /workspace/pdf-test/baidu/.venv/bin/python ]]; then
    PY=/workspace/pdf-test/baidu/.venv/bin/python
  else
    PY="${PYTHON:-python3}"
  fi
fi
for v in $(env | cut -d= -f1 | grep -i baidu | grep -v OCR_BAIDU_ROOT || true); do unset "$v"; done
# 正式入口：仓库副本。缺缓存或输入时由该入口明确报错，不写成绩。
exec unshare -rn env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="${PYTHONPATH:-}" R5_MODS="${R5_MODS:-}" R5_STACK3="${R5_STACK3:-}" M6_LAB_ROOT="${M6_LAB_ROOT:-}" \
  V2_ROOT="${V2_ROOT:-}" REPO_ROOT="${REPO_ROOT:-}" \
  OCR_BAIDU_ROOT="${OCR_BAIDU_ROOT:-}" OCR_PDF_TEST_ROOT="${OCR_PDF_TEST_ROOT:-}" \
  OCR_LAB_CHECKS="${OCR_LAB_CHECKS:-}" \
  "$PY" "$HERE/复跑_第三版.py" "$@"
