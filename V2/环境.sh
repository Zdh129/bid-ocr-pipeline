#!/bin/bash
# 把 V2 补进仓库的缺失模块加入 PYTHONPATH。不改已有的 复跑.sh。
# 用法：source 环境.sh
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
  _V2_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  _V2_HERE="$(cd "$(dirname "$0")" && pwd)"
fi
_REPO="$(cd "$_V2_HERE/.." && pwd)"
_ENG="$_V2_HERE/工程"
_PATHS=(
  "$_V2_HERE/路线代码/rules/v2"
  "$_V2_HERE/路线代码/rules/v3"
  "$_V2_HERE/路线代码/rules/round5"
  "$_V2_HERE/路线代码/rules/round5_method6"
  "$_V2_HERE/路线代码/rules/round3"
  "$_V2_HERE/路线代码/scripts"
  "$_V2_HERE/路线代码/src"
  "$_V2_HERE/路线代码/rules/baseline_c"
  "$_ENG/output/博凯_清零/工具"
  "$_ENG/output/盲测_版面约束/版面分析优化计划/试算/第5轮/综合测试_2776d4b"
  "$_REPO/src"
  "$_REPO/scripts"
)
_JOIN="$(IFS=:; echo "${_PATHS[*]}")"
export PYTHONPATH="${_JOIN}${PYTHONPATH:+:}$PYTHONPATH"
export R5_MODS="$_V2_HERE/路线代码/rules/round5"
export R5_STACK3="$_V2_HERE/路线代码/rules/round3/frozen_stack3.py"
export M6_LAB_ROOT="$_ENG"
export V2_ROOT="$_V2_HERE"
export REPO_ROOT="$_REPO"
unset _PATHS _JOIN

# 数据根：若本机已有原试验目录且用户未设置，则导出，便于离线复跑。
# 原机冻结校验/断网检查需显式设置 OCR_LAB_CHECKS=1。
if [[ -z "${OCR_BAIDU_ROOT:-}" && -d /workspace/pdf-test/baidu ]]; then
  export OCR_BAIDU_ROOT="/workspace/pdf-test/baidu"
fi
if [[ -z "${OCR_PDF_TEST_ROOT:-}" && -d /workspace/pdf-test/baidu ]]; then
  export OCR_PDF_TEST_ROOT="/workspace/pdf-test"
fi
