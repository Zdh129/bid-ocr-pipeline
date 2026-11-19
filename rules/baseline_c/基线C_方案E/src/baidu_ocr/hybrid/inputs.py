"""读取已跑好的 B 标准版、A 的输出与本地渲染图，整理成 rules.py 需要的逐页结构（pt 坐标）。只读，不联网。"""
from __future__ import annotations

import json
import re
from pathlib import Path

DPI = 200.0
PX_PER_PT = DPI / 72.0


def _j(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def b_raw_layout_index(raw: dict) -> dict:
    """B 中间 JSON 的块 id「b-PPP-iii」对应原始 layouts[iii-1]（见 scripts/blind20_build.py）。"""
    idx = {}
    for page in raw["pages"]:
        p = int(page["page_num"]) + 1
        for i, lay in enumerate(page.get("layouts") or [], 1):
            idx[f"b-{p:03d}-{i:03d}"] = {"raw_type": (lay.get("type") or "text").lower(), "score": float(lay.get("score") or 0.0)}
    return idx


def a_chars_for_page(a_dir: Path, page_summary: dict) -> list[dict]:
    """A 原始 small 粒度字框（px→pt）。重试补回的结果与首轮框重叠 >40% 视为重复（与 blind20_build 一致）。"""
    import numpy as np

    p = page_summary["page"]
    api = page_summary["route"]["api"]
    raw = _j(a_dir / "raw" / f"p{p:02d}_main_{api}.json")
    words = list(raw.get("words_result") or [])
    rpath = a_dir / "raw" / f"p{p:02d}_retry_mapped.json"
    if rpath.exists():
        H, W = int(page_summary["height_px"]), int(page_summary["width_px"])
        mask = np.zeros((H, W), np.uint8)
        for w in words:
            l0 = w["location"]
            mask[max(0, l0["top"]):l0["top"] + l0["height"], max(0, l0["left"]):l0["left"] + l0["width"]] = 1
        for rw in _j(rpath):
            l1 = rw["location"]
            sub = mask[max(0, l1["top"]):l1["top"] + l1["height"], max(0, l1["left"]):l1["left"] + l1["width"]]
            if sub.size and sub.mean() > 0.4:
                continue
            words.append(rw)
    out = []
    for w in words:
        chars = w.get("chars")
        if not chars:
            l = w["location"]
            n = max(1, len(w.get("words") or ""))
            chars = [{"char": ch, "location": {"left": l["left"] + k * l["width"] / n, "top": l["top"], "width": l["width"] / n, "height": l["height"]}}
                     for k, ch in enumerate(w.get("words") or "")]
        for ch in chars:
            l = ch["location"]
            bb = [l["left"] / PX_PER_PT, l["top"] / PX_PER_PT, (l["left"] + l["width"]) / PX_PER_PT, (l["top"] + l["height"]) / PX_PER_PT]
            out.append({"ch": ch["char"], "bbox": bb})
    return out


GAP_RE = re.compile(r"（([a-z_]+)）\s*$")


def load_pages(root: Path, local_seals_px: dict[int, list] | None = None) -> list[dict]:
    """root = /workspace/pdf-test/baidu。local_seals_px: {page: [[x0,y0,x1,y1] px]}（本地红章检测结果）。"""
    out = root / "output"
    b = _j(out / "final" / "scheme_b_standard" / "blind20-scan.json")
    a = _j(out / "final" / "scheme_a" / "blind20-scan.json")
    braw = _j(out / "scheme_b_standard" / "raw" / "blind20-scan.parse_result.json")
    lidx = b_raw_layout_index(braw)
    asum = {pg["page"]: pg for pg in _j(out / "scheme_a" / "call_summary.json")["pages"]}
    pages = []
    for pm in b["pages"]:
        p = int(pm["page_number"])
        bblocks = []
        for blk in sorted([x for x in b["blocks"] if x["page"] == p], key=lambda x: x["reading_order"]):
            meta = lidx.get(blk["id"], {"raw_type": "seal" if blk["type"] == "seal" else "text", "score": None})
            bblocks.append({**blk, "raw_type": meta["raw_type"], "score": meta["score"]})
        a_lines, a_gaps = [], []
        for blk in [x for x in a["blocks"] if x["page"] == p]:
            codes = [w.get("code") for w in blk.get("warnings") or []]
            if "coverage_gap" in codes:
                msg = next(w["message"] for w in blk["warnings"] if w.get("code") == "coverage_gap")
                m = GAP_RE.search(msg)
                a_gaps.append({"id": blk["id"], "bbox": blk["bbox"], "gap_class": m.group(1) if m else "unknown"})
            elif blk["type"] in ("paragraph", "list", "title", "header", "footer", "page_number") and (blk.get("text") or "").strip():
                a_lines.append({"id": blk["id"], "bbox": blk["bbox"], "text": blk["text"], "conf": blk.get("confidence")})
        seals_pt = [[v / PX_PER_PT for v in bb] for bb in (local_seals_px or {}).get(p, [])]
        pages.append({
            "page": p, "w_pt": pm["width"], "h_pt": pm["height"],
            "w_px": pm["coordinate_space"]["render_width_px"], "h_px": pm["coordinate_space"]["render_height_px"],
            "b_blocks": bblocks, "a_lines": a_lines, "a_gaps": a_gaps,
            "a_chars": a_chars_for_page(out / "scheme_a", asum[p]), "local_seals": seals_pt,
        })
    return pages
