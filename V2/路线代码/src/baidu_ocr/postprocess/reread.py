"""按置信度二次识别（opt2 新增，通用规则，不看参照）。本模块只做本地的选取与采用；调用在 scripts/opt2_reread.py。

阈值（事先定死，写进报告，不拟合）：
  选取：A（accurate 含位置）每个识别行，probability.average < AVG_T(0.90) 或 probability.min < MIN_T(0.60)，
        且行中心不落在 B 的表格块里（表格交给 LLM；表格不用 6.2）。
  重识：行框外扩 14px、放大 2 倍，按页拼图送 accurate（百度）。
  采用：在 B 正文块（含该行中心的非表格、非印章块）里找与 A 原行最相近的子串 s（相似度 ≥ 0.66）；
        设重识结果 r（置信度 pr）、A 原行 a（置信度 pa）：
        R-a  r 与 a 规范化后相同、且与 s 不同 → 用 r 替换 s（两次百度读法一致，推翻 B）；
        R-b  否则 pr ≥ 0.95 且 pr ≥ pa + 0.05 且 编辑距离(r, s) ≤ max(1, 20%·|s|) → 用 r 替换 s；
        其余不动。
"""
from __future__ import annotations

import json
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

AVG_T, MIN_T, SIM_T = 0.90, 0.60, 0.66
PX_PER_PT = 200 / 72.0


def norm(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", s or "") if not ch.isspace())


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def select_units(a_dir: Path, b_doc: dict) -> list[dict]:
    summ = json.loads((Path(a_dir) / "call_summary.json").read_text(encoding="utf-8"))
    units = []
    for pg in summ["pages"]:
        p, api = pg["page"], pg["route"]["api"]
        raw = json.loads((Path(a_dir) / "raw" / f"p{p:02d}_main_{api}.json").read_text(encoding="utf-8"))
        tabs = [b["bbox"] for b in b_doc["blocks"] if b["page"] == p and b["type"] == "table"]
        for k, w in enumerate(raw.get("words_result") or []):
            pr = w.get("probability") or {}
            if not (pr.get("average", 1) < AVG_T or pr.get("min", 1) < MIN_T):
                continue
            l = w["location"]
            cx, cy = (l["left"] + l["width"] / 2) / PX_PER_PT, (l["top"] + l["height"] / 2) / PX_PER_PT
            if any(t[0] <= cx <= t[2] and t[1] <= cy <= t[3] for t in tabs):
                continue
            if not norm(w.get("words")):
                continue
            units.append({"id": f"p{p:02d}-w{k:03d}", "page": p, "a_text": w["words"], "pa": pr.get("average"), "pa_min": pr.get("min"),
                          "box_px": [l["left"], l["top"], l["left"] + l["width"], l["top"] + l["height"]], "center_pt": [cx, cy]})
    return units


def best_window(text: str, target: str):
    """在 text 里找与 target 最相近的连续子串（原文下标），返回 (i, j, ratio)。"""
    n, m = len(text), len(target)
    best = (0, 0, 0.0)
    for L in range(max(1, m - 2), m + 3):
        for i in range(0, max(1, n - L + 1)):
            r = SequenceMatcher(None, text[i:i + L], target, autojunk=False).ratio()
            if r > best[2]:
                best = (i, i + L, r)
    return best


def apply(doc: dict, results: list[dict], log: list) -> int:
    """results: [{unit..., r_text, pr}]。改 doc 里 B 正文块文字。"""
    n = 0
    for u in results:
        r = u.get("r_text")
        if not r:
            continue
        cx, cy = u["center_pt"]
        blks = [b for b in doc["blocks"] if b["page"] == u["page"] and b["type"] not in ("table", "seal", "image")
                and b.get("verification_status") != "rejected" and (b.get("text") or "").strip()
                and b["bbox"][0] - 2 <= cx <= b["bbox"][2] + 2 and b["bbox"][1] - 2 <= cy <= b["bbox"][3] + 2]
        if not blks:
            continue
        b = blks[0]
        i, j, ratio = best_window(b["text"], u["a_text"])
        if ratio < SIM_T:
            continue
        s = b["text"][i:j]
        if norm(r) == norm(s):
            continue
        rule = None
        if norm(r) == norm(u["a_text"]):
            rule = "R-a"
        elif (u.get("pr") or 0) >= 0.95 and (u.get("pr") or 0) >= (u.get("pa") or 0) + 0.05 and lev(norm(r), norm(s)) <= max(1, round(0.2 * len(norm(s)))):
            rule = "R-b"
        if rule:
            b["text"] = b["text"][:i] + r + b["text"][j:]
            log.append({"step": "reread", "rule": rule, "page": u["page"], "unit": u["id"], "from": s, "to": r, "pa": u.get("pa"), "pr": u.get("pr")})
            n += 1
    return n
