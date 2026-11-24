#!/bin/bash
# V3 新材料端到端：PDF → 百度识别缓存 → 第六轮新版、整合版（两份中间结果）→ V3 最终结果。
# 本脚本只串联已有入口，不含识别逻辑。默认只做离线部分（0 次百度调用）；联网识别必须由用户确认费用后单独执行。
#
# 用法：
#   export OCR_RUN_ISO=/路径/本份材料_运行隔离     # 必填：本份材料的运行隔离目录
#   export OCR_RESULT_DIR=/路径/识别结果            # 可选：两份中间结果的输出目录，默认 $OCR_RUN_ISO/../识别结果
#   bash 新材料_端到端.sh 准备 /路径/材料.pdf       # 建目录、放入 PDF、拷入配置模板（不联网）
#   bash 新材料_端到端.sh 实跑                      # 联网识别（会花钱）：先确认费用，再按提示手动执行
#   bash 新材料_端到端.sh 离线 [V3结果.json]        # 从识别缓存起：build（如未做）→ 方案E/基线C → 第六轮新版、整合版 → V3 最终结果
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/环境.sh"
G="$HERE/工程/output/盲测集_v1/运行_隔离"
ISO="${OCR_RUN_ISO:?未设置环境变量 OCR_RUN_ISO（本份材料的运行隔离目录）}"
export SWISS_ROUTE="${SWISS_ROUTE:-后付费}"
if [[ -z "${PY:-}" ]]; then
  if [[ -n "${OCR_BAIDU_ROOT:-}" && -x "$OCR_BAIDU_ROOT/.venv/bin/python" ]]; then PY="$OCR_BAIDU_ROOT/.venv/bin/python"; else PY="$(command -v python3)"; fi
fi
export PY
cmd="${1:-离线}"
case "$cmd" in
  准备)
    pdf="${2:?用法：bash 新材料_端到端.sh 准备 /路径/材料.pdf}"
    mkdir -p "$ISO/输入" "$ISO/运行"
    [[ -e "$ISO/输入/blind30-scan.pdf" ]] && { echo "已存在 $ISO/输入/blind30-scan.pdf，不覆盖"; exit 2; }
    cp "$pdf" "$ISO/输入/blind30-scan.pdf"
    [[ -e "$ISO/config_live.yaml" ]] || cp "$G/config_live.模板.yaml" "$ISO/config_live.yaml"
    [[ -e "$ISO/恢复配置.json" ]] || cp "$G/恢复配置.模板.json" "$ISO/恢复配置.json"
    echo "已准备：$ISO"
    echo "输入 PDF sha256：$(sha256sum "$ISO/输入/blind30-scan.pdf" | cut -d' ' -f1)"
    echo "下一步（人工）：在 $ISO/config_live.yaml 的 blind30 段改 scan_pdf、scan_sha256、approval；"
    echo "在 $ISO/恢复配置.json 改 页上限、估算、本次后付费上限_元。模板里的数值是原盲测 30 页的，不要直接沿用。"
    ;;
  实跑)
    echo "联网识别会产生百度费用，本脚本不自动执行。确认费用后，在专用配置 $ISO/config_live.yaml 中按原流程打开外发开关，再执行："
    echo "  bash \"$G/一键实跑.sh\""
    echo "（一键实跑.sh 结束时已离线生成两份中间结果；之后执行：bash \"$0\" 离线 只补 V3 最终结果即可）"
    ;;
  离线)
    out="${2:-$ISO/V3结果.json}"
    if [[ ! -e "$ISO/运行/orig/work/output/final/scheme_b_standard/pred_pages.json" ]]; then
      echo "== build：识别原始返回 → 中间 JSON（不联网）"
      bash "$G/离线跑.sh" "$G/run_30.py" build
    fi
    echo "== 方案E → 基线C（不联网）"
    U=$(bash "$G/离线跑.sh" "$G/run_30.py" upath | tail -1)
    bash "$G/离线跑.sh" "$G/predc.py" "$ISO/运行" "$U" "$ISO/运行/预测" blind30
    echo "== 第六轮新版、整合版（不联网）"
    bash "$G/离线跑.sh" "$G/离线出预测.py"
    echo "== V3 最终结果（不联网）"
    bash "$G/离线跑.sh" "$HERE/出结果_新材料.py" --out "$out"
    ;;
  *) echo "未知子命令：$cmd（可用：准备、实跑、离线）"; exit 2;;
esac
