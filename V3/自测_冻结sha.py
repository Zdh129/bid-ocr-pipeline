#!/usr/bin/env python3
"""只核对本快照内冻结参数与清单代码的 sha256。不读数据集、不调外部接口、不写成绩。"""
import hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE / "代码" / "整合版优化"
V6 = CODE / "v3优化/v6探索/v6候选冻结/v6参数.json"
EXP_V6 = "f7f6104d6a74a41f1d03f96cc6b36398ab673cf46bb46681e6d58f9020e330af"
EXP_V5 = "4b53d049c9b6a678fb46400faf6f40862d5b3d6a082e0749c705b0c8942a4122"
EXP_V4 = "52729ec5745fa20808d73c97b3a7e8cf63895c2e5ccb792c0cfe0b663f097284"

def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

failed = 0

def check(label, path, expected):
    global failed
    if not path.is_file():
        print(f"缺失 {label}: {path}")
        failed += 1
        return
    got = sha(path)
    if got != expected:
        print(f"不一致 {label}: {got} != {expected}")
        failed += 1
    else:
        print(f"一致 {label}")

check("v6参数.json", V6, EXP_V6)
check("冻结/v6参数.json", HERE / "冻结/v6参数.json", EXP_V6)
check("v5参数.json", CODE / "v3优化/整合版_v5冻结/v5参数.json", EXP_V5)
check("v4参数.json", CODE / "v3优化/v4冻结/v4参数.json", EXP_V4)

params = json.loads(V6.read_text(encoding="utf-8"))
for rel, expected in params["代码sha256"].items():
    check(rel, (CODE / rel).resolve(), expected)

sys.exit(1 if failed else 0)
