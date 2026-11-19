"""VLM 回填步骤（可配置开关，默认关闭）：把预先拿到的视觉模型结果按两种场景回填进 B 的中间 JSON。

本步骤**自己从不联网**：只消费调用方传入的结果（aux["vlm"] = {"items": [...], "results": {id: {...}}}）。
真正外发只发生在 scripts/bestconfig_v2_run.py 的 `run --live`，且须专用配置 allow_external: true。

场景（各自可开关）：
- missing_line（B 漏读行补漏）：区域来自路线 2 规则（A 有行、B 无块覆盖的 A 行；或 A、B 都没覆盖的空白缺口）。
  VLM 输出非空即在同页按 y 插入新段落块。可选「一致性护栏」agree_guard（不看参照）：
    · 区域来自 A 行：VLM 输出与 A 文本至少共有 1 个非标点字，否则不采用；
    · 区域来自 A/B 都空的缺口：VLM 输出去标点后少于 2 字不采用（单字多为噪点/杂字）。
- handwritten_date（手写日期复核）：用 handwritten_date_blocks() 的可复现规则选块，VLM 输出非空即替换该块文字。
  去重护栏 dedup_guard（不看参照）：6.2 文字（去标点）已是同页某个 B 块文字的子串，且该块与区域横向有重叠、
    竖向间隔 ≤ dedup_gap_pt（默认 100pt）→ 视为 B 已读到、只是块框偏了（如竖排大标题），不插入。
  插入位置：按 y 插到同页第一个中心更靠下的块之前；margin_vertical_last：竖排（高 ≥ 3×宽）且中心在页面左右 15% 边距内的
    边栏小字，追加到本页正文最后（边栏文字按惯例最后读）。
与 A/Unlimited 补漏（fill_gaps）重叠时以 VLM 为准：
- 日期块直接整块替换（覆盖 fill_gaps 对该块的补字）；
- 漏行区域若与 fill_gaps 改过的块相交，且 fill_gaps 补进的字都出现在 VLM 文字里，则撤销该块的补字（以 VLM 插入为准）。
"""
from __future__ import annotations

import re
import unicodedata

DEFAULT = {"enabled": False, "missing_line": True, "handwritten_date": True, "agree_guard": True,
           "dedup_guard": True, "dedup_gap_pt": 100.0, "margin_vertical_last": True}

# 手写日期规则（不看参照）：
#  1) B 版面类型为文字类（text/paragraph），整块文字（NFKC、去空白）完全匹配「[签订]日期[:]YYYY年M月D日」——
#     即合同签署栏里单独成行的待填日期栏（排除「开票日期」等机打字段、正文句中的日期）；
#  2) 同页 B 原始版面里有 hand_sign（手写签名）块——说明是签署页、日期栏是手填的。
DATE_FIELD_RE = re.compile(r"^(签订)?日期:?\d{2,4}年\d{1,2}月\d{1,2}日$")
RULE_TEXT = ("手写日期块 = B 文字块整块匹配 ^(签订)?日期:?\\d{2,4}年\\d{1,2}月\\d{1,2}日$（NFKC、去空白后），"
             "且同页 B 原始版面含 hand_sign（手写签名）块。")


def _n(s: str | None) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def is_date_field(text: str | None) -> bool:
    return bool(DATE_FIELD_RE.match(_n(text)))


def handwritten_date_blocks(blocks: list[dict]) -> list[dict]:
    """blocks：同一文档的 B 块，需带 raw_type（B 原始版面类型）。返回命中的块（按页、阅读顺序）。"""
    sign_pages = {b["page"] for b in blocks if b.get("raw_type") == "hand_sign"}
    out = [b for b in blocks if b["page"] in sign_pages and b.get("type") in ("paragraph", "text")
           and b.get("raw_type") in (None, "text", "paragraph") and is_date_field(b.get("text"))]
    return sorted(out, key=lambda b: (b["page"], b.get("reading_order", 0)))


def _core(s: str) -> str:
    return "".join(c for c in _n(s) if not unicodedata.category(c).startswith(("P", "S", "Z")))


def accept_missing(vlm_text: str, a_text: str, from_a_line: bool) -> tuple[bool, str]:
    t = _core(vlm_text)
    if not t:
        return False, "VLM 输出为空"
    if from_a_line:
        a = _core(a_text)
        if a and not (set(t) & set(a)):
            return False, "与 A 行文字无共同字"
        return True, ""
    if len(t) < 2:
        return False, "A/B 都没覆盖的缺口，VLM 只给出 1 个字"
    return True, ""


def duplicate_of(t: str, bbox, blocks: list[dict], gap_pt: float) -> str | None:
    """t 已出现在附近 B 块里则返回该块 id。"""
    core = _core(t)
    if not core:
        return None
    for b in blocks:
        if b.get("type") == "seal" or b.get("verification_status") == "rejected" or b.get("source") == "vlm":
            continue
        bb = b["bbox"]
        if min(bb[2], bbox[2]) <= max(bb[0], bbox[0]):
            continue
        gap = max(0.0, max(bb[1], bbox[1]) - min(bb[3], bbox[3]))
        if gap <= gap_pt and core in _core(b.get("text")):
            return b["id"]
    return None


def is_margin_vertical(bbox, page_w: float | None) -> bool:
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if not page_w or h < 3 * max(w, 1e-6):
        return False
    cx = (bbox[0] + bbox[2]) / 2
    return cx <= 0.15 * page_w or cx >= 0.85 * page_w


def flatten(s: str) -> str:
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s or "", flags=re.M).strip()
    out = ""
    for p in [x.strip() for x in s.splitlines() if x.strip()]:
        if out and re.match(r"[A-Za-z0-9]", out[-1]) and re.match(r"[A-Za-z0-9]", p[0]):
            out += " "
        out += p
    return out


def _inter(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _prov(item: dict, res: dict, pre) -> dict:
    return {"region_id": item["id"], "route": item.get("route", "bestconfig_v2"), "kind": item["kind"], "reasons": item.get("reasons", []),
            "model": res.get("model"), "crop": item["crop_path"], "crop_sha256": item["sha256"], "crop_box_px": item.get("crop_box_px"),
            "render_dpi": 200, "pre_vlm_text": pre}


def apply(doc: dict, cfg: dict, log: list, aux: dict) -> int:
    vlm = (aux or {}).get("vlm")
    if vlm is None:
        raise ValueError("vlm_backfill 需要 aux['vlm']（预先拿到的视觉模型结果）；本步骤不会自己外发")
    items, results = vlm["items"], vlm["results"]
    by_id = {b["id"]: b for b in doc["blocks"]}
    fills = {e["block_id"]: e for e in log if e.get("step") == "fill_gaps" and "cell" not in e}
    n = 0
    k_new = 0
    for it in items:
        scen = it.get("scenario")
        if not cfg.get(scen, False):
            continue
        res = results.get(it["id"]) or {}
        if not res.get("ok"):
            log.append({"step": "vlm_backfill", "scenario": scen, "item_id": it["id"], "page": it["page"], "action": "skip", "why": res.get("error") or "无结果"})
            continue
        t = flatten(res.get("text") or "")
        p = it["page"]
        if scen == "handwritten_date":
            blk = by_id.get(it["target"]["b_block_id"])
            if blk is None or blk.get("verification_status") == "rejected" or not t:
                log.append({"step": "vlm_backfill", "scenario": scen, "item_id": it["id"], "page": p, "action": "skip", "why": "块不存在/已剔除/VLM 空"})
                continue
            pre = blk.get("text")
            if _n(pre) == _n(t):
                log.append({"step": "vlm_backfill", "scenario": scen, "item_id": it["id"], "page": p, "block_id": blk["id"], "bbox": blk["bbox"],
                            "action": "confirm", "before": pre, "after": t})
                continue
            blk["text"] = t
            blk["source"] = "vlm"
            blk["verification_status"] = "unverified"
            blk["vlm"] = _prov(it, res, pre)
            blk.setdefault("warnings", []).append({"code": "vlm_replaced", "message": f"手写日期由视觉模型复核改写（区域 {it['id']}）", "page": p,
                                                   "block_id": blk["id"], "severity": "提示"})
            log.append({"step": "vlm_backfill", "scenario": scen, "item_id": it["id"], "page": p, "block_id": blk["id"], "bbox": blk["bbox"],
                        "action": "replace", "before": pre, "after": t, "overrode_fill_gaps": blk["id"] in fills})
            n += 1
            continue
        # missing_line
        ok, why = (accept_missing(t, (it.get("context") or {}).get("a_text") or "", "a_line_id" in (it.get("target") or {}))
                   if cfg.get("agree_guard", True) else (bool(_core(t)), "VLM 输出为空"))
        if ok and cfg.get("dedup_guard", True):
            dup = duplicate_of(t, it["bbox_pt"], [b for b in doc["blocks"] if b["page"] == p], float(cfg.get("dedup_gap_pt", 100.0)))
            if dup:
                ok, why = False, f"B 已在附近块 {dup} 读到（块框偏了）"
        if not ok:
            log.append({"step": "vlm_backfill", "scenario": scen, "item_id": it["id"], "page": p, "bbox": it["bbox_pt"], "action": "reject", "text": t, "why": why})
            continue
        reverted = []
        for bid, e in list(fills.items()):
            b = by_id.get(bid)
            if b and b["page"] == p and _inter(b["bbox"], it["bbox_pt"]) > 0 and all(x in t for x in e["inserted"]):
                b["text"] = e["before"]
                reverted.append(bid)
                fills.pop(bid)
        k_new += 1
        bid = f"b-{p:03d}-v{k_new:02d}"
        nb = {"id": bid, "page": p, "bbox": it["bbox_pt"], "bbox_unit": "pt", "type": "paragraph", "text": t, "confidence": 0.8,
              "source": "vlm", "verification_status": "unverified", "vlm": _prov(it, res, None),
              "warnings": [{"code": "vlm_inserted", "message": f"该行 B 未输出，由视觉模型从裁图补出（区域 {it['id']}）", "page": p, "block_id": bid, "severity": "提示"}]}
        page_blocks = sorted([b for b in doc["blocks"] if b["page"] == p], key=lambda b: b["reading_order"])
        pos = len([b for b in page_blocks if b["type"] != "seal"])
        cy = (nb["bbox"][1] + nb["bbox"][3]) / 2
        page_w = next((float(pm.get("width") or 0) for pm in doc.get("pages", []) if int(pm.get("page_number", 0)) == p), None)
        margin = cfg.get("margin_vertical_last", True) and is_margin_vertical(nb["bbox"], page_w)
        for j, b in enumerate(page_blocks):
            if margin:
                break
            if b["type"] == "seal":
                continue
            if (b["bbox"][1] + b["bbox"][3]) / 2 > cy and not (b["type"] == "table" and b["bbox"][1] <= cy <= b["bbox"][3]):
                pos = j
                break
        page_blocks.insert(pos, nb)
        for r, b in enumerate(page_blocks):
            b["reading_order"] = r
        doc["blocks"].append(nb)
        by_id[bid] = nb
        log.append({"step": "vlm_backfill", "scenario": scen, "item_id": it["id"], "page": p, "block_id": bid, "bbox": it["bbox_pt"],
                    "action": "insert", "before": "", "after": t, "reverted_fill_gaps": reverted, "placed": "page_end_margin" if margin else "by_y"})
        n += 1
    doc["blocks"].sort(key=lambda b: (b["page"], b["reading_order"]))
    return n
