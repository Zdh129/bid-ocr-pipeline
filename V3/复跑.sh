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

ORIG="/workspace/pdf-test/baidu/output/分流整合版/整合版优化/v3优化/v6探索"
if [[ ! -f "$ORIG/v6候选冻结/v6参数.json" ]]; then
  echo "未找到原始试验环境中的缓存与冻结目录。导入自测和规则干跑已通过。"
  echo "完整复跑（评分.py）需要本地识别缓存，本仓库不提供数据集或缓存。"
  echo "从零跑一份新材料：先按仓库根 README 生成 scheme_a 缓存，再运行："
  echo "  python3 $HERE/跑新材料.py --pred 预测.json --scheme-a scheme_a目录 --out 结果.json"
  exit 0
fi
if ! cmp -s "$HERE/代码/整合版优化/v3优化/v6探索/v6候选冻结/v6参数.json" "$ORIG/v6候选冻结/v6参数.json"; then
  echo "本地 v6 冻结参数与本快照不一致，已停下。未改文件，也未调用接口。" >&2
  exit 1
fi
for v in $(env | cut -d= -f1 | grep -i baidu || true); do unset "$v"; done
export PYTHONDONTWRITEBYTECODE=1
SETS=("盲测25" "新集8" "65扫描" "新扫描9")
if [[ $# -gt 0 ]]; then SETS=("$@"); fi
CMD=()
for st in "${SETS[@]}"; do
  CMD+=("$PY" "$ORIG/工具/评分.py" "$st" "1+5a" "github_v3" ";")
done
# 去掉最后一个分号
unset "CMD[-1]"
exec unshare -rn env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PYTHONPATH" R5_MODS="$R5_MODS" R5_STACK3="$R5_STACK3" M6_LAB_ROOT="$M6_LAB_ROOT" bash -c "${CMD[*]}"
