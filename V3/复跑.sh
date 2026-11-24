#!/bin/bash
# V3（优化成本）：先核对本快照并导入自测，再在有本地缓存时按 v6 候选离线复跑。
# 只用本地缓存；在无网络命名空间里运行，并去掉所有百度相关环境变量，保证 0 次调用。
# 本仓库不提供数据集或缓存。离线完整复跑需要原始试验环境中的识别缓存。
# 用法：
#   bash 复跑.sh            # 自测；若有本地缓存再跑四套集
#   bash 复跑.sh 盲测25     # 自测后只跑指定集
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/环境.sh"
PY="${PY:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "${REPO_ROOT:-}/../pdf-test/baidu/.venv/bin/python" ]]; then
    PY="${REPO_ROOT}/../pdf-test/baidu/.venv/bin/python"
  elif [[ -x /workspace/pdf-test/baidu/.venv/bin/python ]]; then
    PY=/workspace/pdf-test/baidu/.venv/bin/python
  else
    PY="${PYTHON:-python3}"
  fi
fi

"$PY" "$HERE/自测_冻结sha.py"
"$PY" "$HERE/自测_导入.py"
"$PY" "$HERE/干跑_规则样例.py"

SCORE="$HERE/代码/整合版优化/v3优化/v6探索/工具/评分.py"
for v in $(env | cut -d= -f1 | grep -i baidu | grep -v OCR_BAIDU_ROOT || true); do unset "$v"; done
export PYTHONDONTWRITEBYTECODE=1
SETS=("盲测25" "新集8" "65扫描" "新扫描9")
if [[ $# -gt 0 ]]; then SETS=("$@"); fi
# 正式入口：仓库副本的 评分.py。缺缓存或输入时由该入口明确报错，不写成绩。
# 默认对照组合（与原 v6 组合成绩同一入口形态）。
CMD=()
for st in "${SETS[@]}"; do
  CMD+=("$PY" "$SCORE" "$st" "1+5a" "github_v3_fix" ";")
done
unset "CMD[-1]"
exec unshare -rn env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$PYTHONPATH" R5_MODS="$R5_MODS" R5_STACK3="$R5_STACK3" M6_LAB_ROOT="$M6_LAB_ROOT" \
  V3_ROOT="$V3_ROOT" REPO_ROOT="$REPO_ROOT" \
  OCR_BAIDU_ROOT="${OCR_BAIDU_ROOT:-}" OCR_PDF_TEST_ROOT="${OCR_PDF_TEST_ROOT:-}" \
  OCR_LAB_CHECKS="${OCR_LAB_CHECKS:-}" \
  bash -c "${CMD[*]}"
