#!/bin/bash
# 一份新材料：实跑识别缓存，再离线出第六轮新版与整合版。隔离副本，不改生产配置。
# 【仓库副本：只改路径】原 ISO 固定为试验机 盲测集_v1/运行_隔离；现由环境变量 OCR_RUN_ISO 指定本份材料的运行隔离目录，
# 脚本（run_30.py 等）在本文件同目录，工作目录为仓库根。步骤、闸门与顺序与原脚本相同。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../../../../环境.sh"
ISO="${OCR_RUN_ISO:?未设置环境变量 OCR_RUN_ISO（本份材料的运行隔离目录，含 输入/blind30-scan.pdf、config_live.yaml、恢复配置.json）}"
export SWISS_ROUTE=后付费
# key 只在运行时读，去掉末尾空白（脚本内再剥离一次，这里不 echo 值）
if [ -n "${BAIDU_OCR_API_KEY+x}" ]; then export BAIDU_OCR_API_KEY="${BAIDU_OCR_API_KEY%"${BAIDU_OCR_API_KEY##*[![:space:]]}"}"; fi
if [ -n "${BAIDU_OCR_SECRET_KEY+x}" ]; then export BAIDU_OCR_SECRET_KEY="${BAIDU_OCR_SECRET_KEY%"${BAIDU_OCR_SECRET_KEY##*[![:space:]]}"}"; fi
if [ -e "$ISO/运行/停止.json" ]; then echo "停止标记还在：不调用百度。$ISO/运行/停止.json"; exit 3; fi
cd "$REPO_ROOT"
if [[ -z "${PY:-}" ]]; then
  if [[ -n "${OCR_BAIDU_ROOT:-}" && -x "$OCR_BAIDU_ROOT/.venv/bin/python" ]]; then PY="$OCR_BAIDU_ROOT/.venv/bin/python"; else PY="$(command -v python3)"; fi
fi
export PY
echo "== verify + prep（本地，不调百度）"
$PY "$HERE/run_30.py" verify
$PY "$HERE/run_30.py" prep
echo "== 联网识别（经 capwrap：次数×1.3、现金¥7.50、17/18/19 即停；网络错误有限次重试）"
$PY "$HERE/run_30.py" baidu --live
if [ -e "$ISO/运行/停止.json" ]; then echo "百度步骤出现停止标记，不继续"; exit 6; fi
bash "$HERE/离线跑.sh" "$HERE/run_30.py" build
$PY "$HERE/run_30.py" reread --live
if [ -e "$ISO/运行/停止.json" ]; then echo "二次识别出现停止标记，不继续"; exit 6; fi
$PY "$HERE/run_30.py" vert --live
if [ -e "$ISO/运行/停止.json" ]; then echo "页边竖排出现停止标记，不继续"; exit 6; fi
echo "== 离线组装预测"
U=$(bash "$HERE/离线跑.sh" "$HERE/run_30.py" upath | tail -1)
bash "$HERE/离线跑.sh" "$HERE/predc.py" "$ISO/运行" "$U" "$ISO/运行/预测" blind30
bash "$HERE/离线跑.sh" "$HERE/离线出预测.py"
echo "== 完成：识别结果已写入 ${OCR_RESULT_DIR:-$ISO/../识别结果}/"
