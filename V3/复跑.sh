#!/bin/bash
# V3（优化成本）：在原始试验环境中按 v6 候选（方向1 + 方向5a）离线复跑。
# 只用本地缓存；在无网络命名空间里运行，并去掉所有百度相关环境变量，保证 0 次调用。
# 本仓库不提供数据集或缓存。若本地冻结文件与本快照 sha 不一致，脚本会停下，不会改文件、也不会调用接口。
# 用法：bash 复跑.sh              （四套集都跑）
#       bash 复跑.sh 盲测25        （只跑指定集）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ORIG="/workspace/pdf-test/baidu/output/分流整合版/整合版优化/v3优化/v6探索"
SNAP="$HERE/代码/整合版优化/v3优化/v6探索/v6候选冻结/v6参数.json"
PY="${PY:-/workspace/pdf-test/baidu/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then PY="${PYTHON:-python3}"; fi

if [[ ! -f "$SNAP" ]]; then
  echo "找不到本快照中的 v6 冻结参数：$SNAP" >&2
  exit 1
fi
if [[ ! -f "$ORIG/v6候选冻结/v6参数.json" ]]; then
  echo "找不到原始试验环境中的 v6 冻结文件，本脚本不能在无缓存的环境里复跑。本仓库不提供数据集或缓存。" >&2
  exit 1
fi

# 核对本快照与本地冻结文件一致，不一致就停下。
if ! cmp -s "$SNAP" "$ORIG/v6候选冻结/v6参数.json"; then
  echo "本地 v6 冻结参数与本快照不一致，已停下。未改文件，也未调用接口。" >&2
  exit 1
fi
"$PY" "$HERE/自测_冻结sha.py"

for v in $(env | cut -d= -f1 | grep -i baidu || true); do unset "$v"; done
export PYTHONDONTWRITEBYTECODE=1
SETS=("盲测25" "新集8" "65扫描" "新扫描9")
if [[ $# -gt 0 ]]; then SETS=("$@"); fi
# 输出写到原始试验目录的 成绩/，文件名带 github_v3 前缀，不覆盖既有成绩文件，也不写入本仓库。
CMD=()
for st in "${SETS[@]}"; do
  CMD+=("$PY" "$ORIG/工具/评分.py" "$st" "1+5a" "github_v3")
  CMD+=(";")
done
unset "CMD[-1]"
exec unshare -rn env PYTHONDONTWRITEBYTECODE=1 bash -c "${CMD[*]}"
