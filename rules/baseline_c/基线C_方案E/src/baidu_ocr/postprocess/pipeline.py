"""后处理入口。只在本地改中间 JSON，不联网。"""
from __future__ import annotations

import copy

from . import fill_gaps, invoice_reorder, seal_strip, star, vlm_backfill

DEFAULT_CFG = {
    "enabled": True,
    "star": {"enabled": True, "serial_dot": True},
    "seal_strip": {"enabled": True, "use_position": True, "use_text": True, "overlap_min": 0.6, "max_chars": 12,
                   "block_types": ["paragraph", "header"]},
    "fill_gaps": {"enabled": True, "max_len": 2, "min_sim": 0.6, "a_pad_x": 10.0},
    "invoice_reorder": {"enabled": True},
    # VLM 回填：默认关闭；本步骤只消费预先拿到的结果（aux["vlm"]），自身不外发
    "vlm_backfill": dict(vlm_backfill.DEFAULT),
}
ORDER = ("star", "seal_strip", "fill_gaps", "invoice_reorder", "vlm_backfill")


def merged_cfg(cfg: dict | None) -> dict:
    out = copy.deepcopy(DEFAULT_CFG)
    for k, v in (cfg or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def run_postprocess(doc: dict, cfg: dict | None = None, aux: dict | None = None) -> tuple[dict, dict]:
    """返回 (新文档, 日志)。cfg 为 config.yaml 的 postprocess 节；aux 为 aux.load_aux() 结果（补漏/重排需要）。"""
    c = merged_cfg(cfg)
    doc = copy.deepcopy(doc)
    log: list = []
    counts = {}
    if not c.get("enabled", True):
        return doc, {"counts": counts, "log": log, "cfg": c}
    for step in ORDER:
        sc = c.get(step) or {}
        if not sc.get("enabled", False):
            continue
        if step == "star":
            counts[step] = star.apply(doc, sc, log)
        elif step == "seal_strip":
            counts[step] = seal_strip.apply(doc, sc, log)
        elif step == "vlm_backfill":
            counts[step] = vlm_backfill.apply(doc, sc, log, aux)
        elif step in ("fill_gaps", "invoice_reorder"):
            if aux is None:
                raise ValueError(f"{step} 需要 aux（A 字框与 Unlimited 输出）")
            counts[step] = (fill_gaps if step == "fill_gaps" else invoice_reorder).apply(doc, sc, log, aux)
    doc.setdefault("warnings", []).append({"code": "postprocess", "message": "已做后处理：" + "、".join(
        f"{k}×{v}" for k, v in counts.items()), "severity": "提示"})
    return doc, {"counts": counts, "log": log, "cfg": c}


def pred_pages(doc: dict) -> dict:
    """与 blind20_build / hybrid.merge.pred_pages_from_doc 同口径，但跳过 rejected 块。"""
    out = {}
    for pm in doc["pages"]:
        p = int(pm["page_number"])
        bl = sorted([x for x in doc["blocks"] if x["page"] == p and x.get("verification_status") != "rejected"],
                    key=lambda x: x["reading_order"])
        secs = []
        for x in bl:
            if x["type"] == "table":
                rows: dict[int, list] = {}
                for c in sorted(x["table"]["cells"], key=lambda c: (c["row"], c["col"])):
                    rows.setdefault(c["row"], []).append(c["text"])
                secs.append({"section": "table", "text": "\n".join(" ".join(v) for _, v in sorted(rows.items()))})
            elif x["type"] == "seal":
                continue
            else:
                secs.append({"section": "body", "text": x.get("text") or ""})
        seal = "\n".join(x["text"] for x in bl if x["type"] == "seal" and x.get("text"))
        out[str(p)] = {"sections": secs, "seal": seal}
    return out
