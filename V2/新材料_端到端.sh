#!/bin/bash
# V2 新材料端到端：PDF → 百度识别缓存 → 方案E/基线C → V2（候选路线第三版）结果。
# 本脚本只串联已有入口，不含识别逻辑。上游（准备、实跑、build、方案E/基线C）复用 V3 补进来的
# V3/工程/output/盲测集_v1/运行_隔离/ 下的 run_30.py、predc.py 等（与 V3 新材料入口完全同一套）；
# 最后一步用 出结果_新材料.py 调 复跑_第三版.py 的底座与各层。默认只做离线部分（0 次百度调用）；联网识别必须由用户确认费用后单独执行。
#
# 用法：
#   export OCR_RUN_ISO=/路径/本份材料_运行隔离     # 必填：本份材料的运行隔离目录
#   bash 新材料_端到端.sh 准备 /路径/材料.pdf       # 建目录、放入 PDF、拷入配置模板（不联网）
#   bash 新材料_端到端.sh 实跑                      # 联网识别（会花钱）：只打印步骤，不自动执行
#   bash 新材料_端到端.sh 离线 [V2结果.json]        # 从识别缓存起：build（如未做）→ 方案E/基线C → V2 结果（默认 $OCR_RUN_ISO/V2结果.json）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
V3D="$REPO/V3"
G="$V3D/工程/output/盲测集_v1/运行_隔离"
ISO="${OCR_RUN_ISO:?未设置环境变量 OCR_RUN_ISO（本份材料的运行隔离目录）}"
export SWISS_ROUTE="${SWISS_ROUTE:-后付费}"
if [[ -z "${PY:-}" ]]; then
  if [[ -n "${OCR_BAIDU_ROOT:-}" && -x "$OCR_BAIDU_ROOT/.venv/bin/python" ]]; then PY="$OCR_BAIDU_ROOT/.venv/bin/python"; else PY="$(command -v python3)"; fi
fi
export PY
# 上游步骤用 V3 的环境（由 离线跑.sh 自己 source V3/环境.sh）；先去掉 V2 环境变量，避免两版模块路径混用。
up() { env -u PYTHONPATH -u R5_MODS -u R5_STACK3 -u M6_LAB_ROOT -u V2_ROOT -u REPO_ROOT -u V3_ROOT bash "$G/离线跑.sh" "$@"; }
cmd="${1:-离线}"
case "$cmd" in
  准备)
    exec bash "$V3D/新材料_端到端.sh" 准备 "${2:?用法：bash 新材料_端到端.sh 准备 /路径/材料.pdf}"
    ;;
  实跑)
    echo "联网识别会产生百度费用，本脚本不自动执行。确认费用后，在专用配置 $ISO/config_live.yaml 中按原流程打开外发开关，再执行："
    echo "  bash \"$G/一键实跑.sh\""
    echo "（识别缓存与 V3 共用；之后执行：bash \"$0\" 离线 出 V2 结果）"
    ;;
  离线)
    out="${2:-$ISO/V2结果.json}"
    if [[ ! -e "$ISO/运行/orig/work/output/final/scheme_b_standard/pred_pages.json" ]]; then
      echo "== build：识别原始返回 → 中间 JSON（不联网）"
      up "$G/run_30.py" build
    fi
    echo "== 方案E → 基线C（不联网）"
    U=$(up "$G/run_30.py" upath | tail -1)
    up "$G/predc.py" "$ISO/运行" "$U" "$ISO/运行/预测" blind30
    echo "== V2 结果（不联网）"
    # shellcheck source=/dev/null
    source "$HERE/环境.sh"
    for v in $(env | cut -d= -f1 | grep -i baidu | grep -v '^OCR_BAIDU_ROOT$' || true); do unset "$v"; done
    exec unshare -rn env PYTHONDONTWRITEBYTECODE=1 "$PY" "$HERE/出结果_新材料.py" --out "$out"
    ;;
  *) echo "未知子命令：$cmd（可用：准备、实跑、离线）"; exit 2;;
esac
