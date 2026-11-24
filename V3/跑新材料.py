#!/usr/bin/env python3
"""从一份已有预测上离线套用 V3 方向1、方向5a。0 次百度调用。

用法：
  source 环境.sh
  python3 跑新材料.py --pred 预测.json --scheme-a scheme_a目录 --out 结果.json

预测 JSON：{ "页号": { "sections": [...], "seal": "..." }, ... }
scheme-a 目录：高精度含位置的缓存（含 raw/ 与 call_summary.json，格式与原试验工程相同）。
没有缓存时不要调用百度，请先按仓库根 README「如何运行」在确认费用后生成缓存。
完整 v6（含 v5 底座的旋转/分流等）入口是 公共v6.v6，它在 import 时会读测试集路径，需要本地缓存，见 复跑.sh。
"""
from __future__ import annotations
import argparse, json, sys
from copy import deepcopy
from pathlib import Path

def main() -> int:
    p = argparse.ArgumentParser(description="离线套用 V3 方向1 与方向5a")
    p.add_argument("--pred", required=True, help="已有预测 JSON")
    p.add_argument("--scheme-a", required=True, help="该份材料的高精度 scheme_a 缓存目录")
    p.add_argument("--out", required=True, help="输出预测 JSON")
    args = p.parse_args()
    import 规则v6 as G
    pred = json.loads(Path(args.pred).read_text(encoding="utf-8"))
    sa = Path(args.scheme_a)
    if not sa.is_dir():
        print(f"找不到 scheme_a 目录：{sa}。离线复跑需要缓存，请先生成识别缓存。", file=sys.stderr)
        return 2
    out = {}
    logs = []
    for k, pg in pred.items():
        try:
            q = int(k)
        except (TypeError, ValueError):
            q = k
        cur = deepcopy(pg)
        cur, lg1 = G.mixed_rebuild(cur, sa, q)
        if lg1:
            logs.append({"页": k, "规则": 1, **(lg1 if isinstance(lg1, dict) else {"log": lg1})})
        cur, lg5 = G.split_cols(cur, sa, q, mode="a")
        if lg5:
            logs.append({"页": k, "规则": "5a", **(lg5 if isinstance(lg5, dict) else {"log": lg5})})
        out[k] = cur
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"pred": out, "规则日志": logs}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写完 {args.out}，触发日志 {len(logs)} 条")
    return 0

if __name__ == "__main__":
    sys.exit(main())
