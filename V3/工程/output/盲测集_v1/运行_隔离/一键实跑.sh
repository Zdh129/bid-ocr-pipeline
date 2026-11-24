#!/bin/bash
# 盲测集_v1 桓齐+荣和 30 页：实跑识别缓存，再离线出第六轮新版与整合版。隔离副本，不改生产配置。
set -euo pipefail
ISO=/workspace/pdf-test/baidu/output/盲测集_v1/运行_隔离
export SWISS_ROUTE=后付费
# key 只在运行时读，去掉末尾空白（脚本内再剥离一次，这里不 echo 值）
if [ -n "${BAIDU_OCR_API_KEY+x}" ]; then export BAIDU_OCR_API_KEY="${BAIDU_OCR_API_KEY%"${BAIDU_OCR_API_KEY##*[![:space:]]}"}"; fi
if [ -n "${BAIDU_OCR_SECRET_KEY+x}" ]; then export BAIDU_OCR_SECRET_KEY="${BAIDU_OCR_SECRET_KEY%"${BAIDU_OCR_SECRET_KEY##*[![:space:]]}"}"; fi
if [ -e "$ISO/运行/停止.json" ]; then echo "停止标记还在：不调用百度。$ISO/运行/停止.json"; exit 3; fi
cd /workspace/pdf-test/baidu
PY=.venv/bin/python
echo "== verify + prep（本地，不调百度）"
$PY "$ISO/run_30.py" verify
$PY "$ISO/run_30.py" prep
echo "== 联网识别（经 capwrap：次数×1.3、现金¥7.50、17/18/19 即停；网络错误有限次重试）"
$PY "$ISO/run_30.py" baidu --live
if [ -e "$ISO/运行/停止.json" ]; then echo "百度步骤出现停止标记，不继续"; exit 6; fi
bash "$ISO/离线跑.sh" "$ISO/run_30.py" build
$PY "$ISO/run_30.py" reread --live
if [ -e "$ISO/运行/停止.json" ]; then echo "二次识别出现停止标记，不继续"; exit 6; fi
$PY "$ISO/run_30.py" vert --live
if [ -e "$ISO/运行/停止.json" ]; then echo "页边竖排出现停止标记，不继续"; exit 6; fi
echo "== 离线组装预测"
U=$(bash "$ISO/离线跑.sh" "$ISO/run_30.py" upath | tail -1)
bash "$ISO/离线跑.sh" "$ISO/predc.py" "$ISO/运行" "$U" "$ISO/运行/预测" blind30
bash "$ISO/离线跑.sh" "$ISO/离线出预测.py"
echo "== 完成：识别结果已写入 盲测集_v1/识别结果/"
