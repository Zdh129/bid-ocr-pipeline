#!/usr/bin/env python3
"""只核对本快照内冻结参数与清单代码的 sha256。不读数据集、不调外部接口、不写成绩。

参数 json 的 sha 必须与原冻结一致。只修了路径/依赖加载的代码文件，对照修复后 sha；
原冻结 sha 仍打印对照，见仓库根目录 修复说明.md。
"""
import hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE / "代码" / "整合版优化"
V6 = CODE / "v3优化/v6探索/v6候选冻结/v6参数.json"
EXP_V6 = "f7f6104d6a74a41f1d03f96cc6b36398ab673cf46bb46681e6d58f9020e330af"
EXP_V5 = "4b53d049c9b6a678fb46400faf6f40862d5b3d6a082e0749c705b0c8942a4122"
EXP_V4 = "52729ec5745fa20808d73c97b3a7e8cf63895c2e5ccb792c0cfe0b663f097284"

# rel -> (原冻结 sha, 修复后 sha)。未列入的文件必须仍与原冻结一致。
PATH_FIX_SHA = {
    "v3优化/工具/公共v3.py": (
        "c8b09d88d867dc5244c7f08216c80f1d4bf338b906012e6baa0a336787c53d56",
        "2ea412c9bd7db1a74985a2cc626cf7a50bac292bcc7c6421bcb5491960c2693e",
    ),
    "v3优化/v6探索/工具/公共v6.py": (
        "d3bd33c8c5f16318160f69b77290f3c6ed261fd44157d6d1c2ba8e921b8fbaad",
        "4bccc1a56a436ef2ff57401b2c36095238043f20c5ceae6ee60ce9bfb3304e27",
    ),
    "v3优化/工具/新扫描9页.py": (
        "640aa84ea5069ae07d617a54840cbb70e8e4f19bb848c5b1ec44f81e42a39a00",
        "0a925e228cd12cf8673805b2c0aaf6976a1401f283a4d9a094a8147ecf89b2f9",
    ),
    "v3优化/v6探索/工具/规则v6.py": (
        "5e7e18992bbb6e827d15017bc44d03abe1d1cf016a313c32d462c7e9385fff4c",
        "4deb56e945ad6300c71e5b4b20248d3027fcc7822ea08a7f670ecb4e715eb2de",
    ),
}

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
    if rel in PATH_FIX_SHA:
        old, new = PATH_FIX_SHA[rel]
        if old != expected:
            print(f"对照表原冻结 sha 与参数 json 不一致 {rel}")
            failed += 1
        check(rel + "（修复后）", (CODE / rel).resolve(), new)
        print(f"对照 原冻结 sha {old}")
    else:
        check(rel, (CODE / rel).resolve(), expected)

sys.exit(1 if failed else 0)
