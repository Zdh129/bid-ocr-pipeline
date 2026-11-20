"""核对 rules/ 下冻结文件的 sha256。不读取、不打印任何密钥。"""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from pipeline.paths import REPO_ROOT

RULE_DIRS = {
    "baseline_c": REPO_ROOT / "rules" / "baseline_c",
    "round1": REPO_ROOT / "rules" / "round1",
    "round2": REPO_ROOT / "rules" / "round2",
    "round3": REPO_ROOT / "rules" / "round3",
}

# 冻结记录里「依赖」条目的原始相对路径 → 本仓库路径
DEP_MAP = {
    "output/盲测_版面约束/冻结规则": RULE_DIRS["baseline_c"],
    "output/盲测_版面约束/版面分析优化计划/试算/冻结_新盲测前": RULE_DIRS["round1"],
    "output/盲测_版面约束/版面分析优化计划/试算/第2轮/冻结": RULE_DIRS["round2"],
    "output/盲测_版面约束/版面分析优化计划/试算/第3轮/冻结": RULE_DIRS["round3"],
    "output/盲测_版面约束/版面分析优化计划/试算/第2轮/route_r1_cli.py": RULE_DIRS["round2"] / "route_r1_cli.py",
    "output/synth18e_prep/frozen_方案E.json": REPO_ROOT / "output" / "synth18e_prep" / "frozen_方案E.json",
    "src": RULE_DIRS["baseline_c"] / "基线C_方案E" / "src",
    "scripts": RULE_DIRS["baseline_c"] / "基线C_方案E" / "scripts",
    "config_方案E.yaml": RULE_DIRS["baseline_c"] / "基线C_方案E" / "config_方案E.yaml",
    "config.yaml": RULE_DIRS["baseline_c"] / "基线C_方案E" / "config.yaml",
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_listed(base: Path, listed: str) -> Path:
    if (base / listed).exists():
        return base / listed
    if listed in DEP_MAP:
        return Path(DEP_MAP[listed])
    for prefix, dest in sorted(DEP_MAP.items(), key=lambda kv: -len(kv[0])):
        if listed == prefix:
            return Path(dest)
        if listed.startswith(prefix + "/"):
            return Path(dest) / listed[len(prefix) + 1 :]
    # 相对仓库根，优先冻结快照
    cand = [
        RULE_DIRS["baseline_c"] / "基线C_方案E" / listed,
        REPO_ROOT / listed,
    ]
    for c in cand:
        if c.exists():
            return c
    return base / listed


def check_record(md_path: Path, base: Path) -> list[str]:
    text = md_path.read_text(encoding="utf-8")
    errors = []
    for fname, expect in re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", text):
        p = resolve_listed(base, fname)
        if not p.exists():
            errors.append(f"缺失 {fname}（期望位于 {p}）")
            continue
        got = sha256(p)
        if got != expect:
            errors.append(f"不匹配 {fname}")
    return errors


def main(ns: argparse.Namespace | None = None) -> int:
    names = ["baseline_c", "round1", "round2", "round3"]
    if ns is not None and getattr(ns, "round_name", "all") != "all":
        names = [ns.round_name]
    failed = 0
    for name in names:
        d = RULE_DIRS[name]
        rec = d / "冻结记录.md"
        errs = check_record(rec, d)
        # 同时核对本目录 冻结记录_sha256.txt（若有）
        lst = d / "冻结记录_sha256.txt"
        if lst.exists():
            for line in lst.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                h, _, rest = line.partition(" ")
                # 文件名可能是绝对试验路径，只取末尾名
                fn = Path(rest.strip()).name
                p = d / fn
                if not p.exists():
                    errs.append(f"{lst.name}: 缺失 {fn}")
                elif sha256(p) != h:
                    errs.append(f"{lst.name}: 不匹配 {fn}")
        if errs:
            failed += 1
            print(f"{name}: 失败 {len(errs)} 项")
            for e in errs:
                print("  -", e)
        else:
            print(f"{name}: 通过（与冻结记录 sha256 一致）")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="核对冻结规则 sha256")
    ap.add_argument("--round", dest="round_name", default="all", choices=["all", "baseline_c", "round1", "round2", "round3"])
    raise SystemExit(main(ap.parse_args()))
