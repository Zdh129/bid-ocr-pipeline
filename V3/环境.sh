#!/bin/bash
# 把 V3 补进仓库的依赖目录加入 PYTHONPATH，并设置 R5_MODS / R5_STACK3 / M6_LAB_ROOT。
# 用法：source 环境.sh   或  . 环境.sh
# 不导出任何密钥。离线导入与规则干跑不需要密钥。
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
  _V3_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  _V3_HERE="$(cd "$(dirname "$0")" && pwd)"
fi
_REPO="$(cd "$_V3_HERE/.." && pwd)"
_ENG="$_V3_HERE/工程"
_RULES="$_ENG/output/swisstransfer盲测/组合五副本/路线代码/rules"
_PATHS=(
  "$_V3_HERE/代码/整合版优化/v3优化/v6探索/工具"
  "$_V3_HERE/代码/整合版优化/v3优化/工具"
  "$_V3_HERE/代码/整合版优化/工具"
  "$_V3_HERE/代码/整合版优化/旋转页规则/工具"
  "$_V3_HERE/代码/候选路线_v2/工具"
  "$_RULES/v2"
  "$_RULES/v3"
  "$_RULES/v4"
  "$_RULES/v5"
  "$_RULES/round5"
  "$_RULES/round5_method6"
  "$_RULES/round3"
  "$_ENG/output/整合版_裸高精度底座/工具"
  "$_ENG/output/synth18e专项优化/工具"
  "$_ENG/output/第三版_多方法测试/工具"
  "$_ENG/output/第三版_规则逐条消融/工具"
  "$_ENG/output/博凯_清零/工具"
  "$_ENG/output/盲测_版面约束/版面分析优化计划/试算/第5轮/综合测试_2776d4b"
  "$_ENG/output/盲测集_v1/给ChatGPT"
  "$_ENG/output/盲测集_v1/运行_隔离"
  "$_ENG/output/第六轮_组合五乙1优化/工具"
  "$_REPO/src"
  "$_REPO/scripts"
  "$_REPO/rules/baseline_c"
  "$_REPO/rules/round3"
  "$_REPO/rules/round5"
  "$_REPO/rules/round5_method6"
  "$_RULES/baseline_c"
)
_JOIN="$(IFS=:; echo "${_PATHS[*]}")"
export PYTHONPATH="${_JOIN}${PYTHONPATH:+:}${PYTHONPATH:-}"
export R5_MODS="$_RULES/round5"
export R5_STACK3="$_RULES/round3/frozen_stack3.py"
export M6_LAB_ROOT="$_ENG"
export V3_ROOT="$_V3_HERE"
export REPO_ROOT="$_REPO"
unset _PATHS _JOIN _RULES

# 数据根：若本机已有原试验目录且用户未设置，则导出，便于离线复跑。
# 不在此写入密钥。原机冻结校验/断网检查需显式设置 OCR_LAB_CHECKS=1。
if [[ -z "${OCR_BAIDU_ROOT:-}" && -d /workspace/pdf-test/baidu ]]; then
  export OCR_BAIDU_ROOT="/workspace/pdf-test/baidu"
fi
if [[ -z "${OCR_PDF_TEST_ROOT:-}" && -d /workspace/pdf-test/baidu ]]; then
  export OCR_PDF_TEST_ROOT="/workspace/pdf-test"
fi
