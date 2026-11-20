#!/usr/bin/env python3
"""synth18f：只重发「文档解析标准版·原图」这一步的包装（本地补测_1119 新写，未运行；不改 synth18f_run.py 和冻结清单）。

synth18f_run.py baidu 本身已有「已完成就跳过」的判断（第 232–236 行）：
  - 文档解析／无限量：out/raw/*.parse_result.json 存在即跳过；
  - 高精度：out/call_summary.json 存在即跳过。
上次文档解析返回 282005（quota exceed），没有 parse_result.json，所以直接重跑 baidu 只会重发这一步。
本包装多加两道保险，然后才调用原 baidu()：
  1. 先确认其余四步的完成标记都在（任何一个不在就拒绝，避免顺带重发已付费的接口）；
  2. 确认文档解析这一步确实没有结果；
  3. 只允许 18 页 × ¥0.05 = ¥0.90 的最坏费用，并要求 --live --allow-external 两个开关同时给出。
用法（须等用户在百度控制台开通文档解析付费、并亲自批准后）：
  预演：.venv/bin/python "scripts/本地补测_1119/synth18f_只重发文档解析.py"
  外发：.venv/bin/python "scripts/本地补测_1119/synth18f_只重发文档解析.py" --live --allow-external
之后从 build 续跑：synth18f_run.py build → reread --live → seal --live → vert --live → 计分前记 sha → synth18f_score_加F1.py（一次性）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import synth18f_run as R  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--allow-external", action="store_true")
    a = ap.parse_args()
    has_parse = lambda out: (out / "raw").exists() and any((out / "raw").glob("*.parse_result.json"))  # noqa: E731
    others = {"无限量识别_原图": has_parse(R.WO / "output" / "scheme_b_unlimited"),
              "无限量识别_颜色去章图": has_parse(R.WP / "output" / "scheme_b_unlimited"),
              "高精度识别_原图原走法": (R.WO / "output" / "scheme_a" / "call_summary.json").exists(),
              "高精度识别_颜色去章图": (R.WP / "output" / "scheme_a" / "call_summary.json").exists()}
    miss = [k for k, v in others.items() if not v]
    if miss:
        sys.exit(f"拒绝：这些步骤没有完成标记，直接跑会重发：{miss}")
    if has_parse(R.WO / "output" / "scheme_b_standard"):
        sys.exit("文档解析已有结果，不需要重发")
    print("只会重发：文档解析_原图，18 页，最坏 ¥0.90；其余四步已完成、会被跳过")
    if a.live and not a.allow_external:
        sys.exit("拒绝：外发需要同时给 --live 和 --allow-external")
    R.baidu(argparse.Namespace(live=a.live, cmd="baidu"))


if __name__ == "__main__":
    main()
