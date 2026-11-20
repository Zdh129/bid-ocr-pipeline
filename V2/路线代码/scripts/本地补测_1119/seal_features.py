"""变体一乙候选块的几何特征（不读参照）。被删块的真字/章文标注只在评估时从开发成绩第二版的 seal_diag 里取。"""
import json
import statistics
from common import ctx, SETS, ROOT


def feats(c):
    blocks = {b["id"]: b for b in c.doc["blocks"]}
    out = []
    for u in c.seal:
        b = blocks[u["id"]]
        p = b["page"]
        x0, y0, x1, y1 = b["bbox"]
        w, h = x1 - x0, y1 - y0
        txt = "".join((b.get("text") or "").split())
        seals = [s for s in c.doc["blocks"] if s["page"] == p and s["type"] == "seal"]
        # 与重叠最大的印章块：其外接椭圆（印章框内切椭圆）
        import seal_leak_geo as G
        s = max(seals, key=lambda s: G.ov(b["bbox"], s["bbox"])) if seals else None
        outside = G.outside_ellipse(b["bbox"], s["bbox"]) if s else 1.0
        lines = [bb["bbox"][3] - bb["bbox"][1] for bb in c.doc["blocks"] if bb["page"] == p and bb["type"] in ("paragraph", "text", "list", "header", "title")
                 and len("".join((bb.get("text") or "").split())) >= 2 and (bb["bbox"][3] - bb["bbox"][1]) < 3 * max(1, (bb["bbox"][2] - bb["bbox"][0]) / max(1, len("".join((bb.get("text") or "").split()))))]
        med = statistics.median(lines) if lines else h
        out.append({"page": p, "id": u["id"], "text": txt, "type": b["type"], "w": round(w, 1), "h": round(h, 1),
                    "aspect": round(w / h, 2) if h else 0, "char_w": round(w / max(1, len(txt)), 1), "h_over_med": round(h / med, 2) if med else 0,
                    "outside_ellipse": round(outside, 2), "red": u["red_frac"],
                    "seal_wh": [round(s["bbox"][2] - s["bbox"][0], 1), round(s["bbox"][3] - s["bbox"][1], 1)] if s else None})
    return out


if __name__ == "__main__":
    for n in SETS:
        c, d, _ = ctx(n)
        diag = {x["block_id"]: x for x in json.load(open(ROOT / "output/配置甲/开发成绩_第二版" / n / "scores.json"))["seal_diag"]}
        for f in feats(c):
            x = diag.get(f["id"])
            lab = "未删" if not x else ("误删真字" if x["真字（在参照正文或表格里）"] else "章文")
            print(n, lab, json.dumps(f, ensure_ascii=False))
