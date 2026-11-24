#!/usr/bin/env python3
"""用内存里的最小样例调用 V3 方向1、方向5a，确认不再出现 ModuleNotFoundError。
不读缓存目录、不调百度。样例可能不会触发规则（规则有字数和版式门槛），只验证函数可调用。
用法：先 source 环境.sh，再 python3 干跑_规则样例.py
"""
from copy import deepcopy
import sys

def main() -> int:
    import 规则v6 as G
    pg = {"sections": [{"section": "body", "text": "左侧标题\n左侧正文"}], "seal": ""}
    out, log = G.mixed_rebuild(deepcopy(pg), "no-such-scheme-a", 1)
    print("方向1 mixed_rebuild 返回", type(out).__name__, "日志", bool(log))
    out2, log2 = G.split_cols(deepcopy(pg), "no-such-scheme-a", 1, mode="a")
    print("方向5a split_cols 返回", type(out2).__name__, "日志", bool(log2))
    # 证件页判定（不看文件名）
    print("is_licence", G.is_licence(pg))
    return 0

if __name__ == "__main__":
    sys.exit(main())
