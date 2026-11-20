"""任务2：变体一乙「误删红色标题字」缓解变体（只做开发探索，不并入主路径，不冻结）。

做法：不改 seal_leak.py；在内存里从变体一乙的候选块里剔除满足「保留」规则的块，再用配置甲的开关跑后处理（只用缓存）。
保留规则（通用，不读参照，不按页号/字符写死；只用文档解析的块框、块类型、同页印章块框）：
  甲 形状：块是横向长条（宽/高 ≥ 5）且字大（块高 ≥ 同页正文行高中位数的 1.5 倍）——印章里的环形字、章名块高小或不够长；
  乙 章外：块框落在「重叠最大的那个印章块」的内切椭圆之外的比例 ≥ 0.5——印章文字都在椭圆内；
  丙 标题类：文档解析把块标为 title/header。
  合并 = 甲 或 乙 或 丙。
阈值是看过四套候选块的几何特征（不看参照）后定的整数值，留了余量；但真误删样本只有 3 个，存在过拟合风险，必须在新的留出集上验证。
"""
import copy
import json
import statistics
from common import ctx, SETS, OUTD, ROOT
import jia_eval as J
import seal_leak_geo as G
from newerr_fix import fixed

ASPECT, HMED, OUT_T = 5.0, 1.5, 0.5
TITLE_TYPES = ("title", "header")


def page_line_h(doc, p):
    hs = []
    for bb in doc["blocks"]:
        if bb["page"] != p or bb["type"] not in ("paragraph", "list"):
            continue
        t = "".join((bb.get("text") or "").split())
        h, w = bb["bbox"][3] - bb["bbox"][1], bb["bbox"][2] - bb["bbox"][0]
        if len(t) >= 2 and h < 3 * w / len(t):   # 单行块
            hs.append(h)
    return statistics.median(hs) if hs else None


def rules(doc, u):
    b = next(x for x in doc["blocks"] if x["id"] == u["id"])
    p = b["page"]
    x0, y0, x1, y1 = b["bbox"]
    w, h = x1 - x0, y1 - y0
    med = page_line_h(doc, p)
    seals = [s for s in doc["blocks"] if s["page"] == p and s["type"] == "seal"]
    s = max(seals, key=lambda s: G.ov(b["bbox"], s["bbox"])) if seals else None
    out = G.outside_ellipse(b["bbox"], s["bbox"]) if s else 1.0
    f = {"aspect": round(w / h, 2) if h else 0, "h_over_med": round(h / med, 2) if med else None, "outside_ellipse": round(out, 2), "type": b["type"]}
    r = {"甲": bool(h and w / h >= ASPECT and med and h >= HMED * med), "乙": out >= OUT_T, "丙": b["type"] in TITLE_TYPES}
    return r, f


VARS = {"缓解甲：形状（横向长条＋字大）": ("甲",), "缓解乙：在印章椭圆外": ("乙",), "缓解丙：文档解析标题类": ("丙",), "缓解合并：甲或乙或丙": ("甲", "乙", "丙")}


def main():
    allres = {}
    for n in SETS:
        c, d, _ = ctx(n)
        seal0 = c.seal
        feats = {u["id"]: rules(c.doc, u) for u in seal0}
        preds, counts = {}, {}
        from baidu_ocr.postprocess import pred_pages
        preds[J.B_NAME] = pred_pages(copy.deepcopy(c.doc))
        for name, v in ((J.E_NAME, J.E), (J.R3N, J.REC_NO3), (J.JIA_NAME, J.JIA), (J.NOSEAL, {k: x for k, x in J.JIA.items() if k != "seal"})):
            preds[name], counts[name], _ = c.run(v, llm=None)
        kept = {}
        for vn, ks in VARS.items():
            c.seal = [u for u in seal0 if not any(feats[u["id"]][0][k] for k in ks)]
            kept[vn] = [u["id"] for u in seal0 if any(feats[u["id"]][0][k] for k in ks)]
            preds[vn], counts[vn], lg = c.run(J.JIA, llm=None)
            kept[vn + "·删掉的块"] = [(x["page"], x["removed"]) for x in lg if x.get("step") == "seal_leak"]
        c.seal = seal0
        _, lg = None, None
        _p, _c, lgj = c.run(J.JIA, llm=None)
        jia_removed = [(x["page"], x["removed"], x["block_id"]) for x in lgj if x.get("step") == "seal_leak"]
        res, scorer = J.score(preds, d["dir"])
        rows = {k: r["rows"] for k, r in res.items()}
        newerr = {}
        for vn in list(VARS) + [J.JIA_NAME]:
            for base in (J.NOSEAL, J.JIA_NAME, J.E_NAME, J.R3N):
                if base != vn:
                    old, add, _ = fixed(rows[base], rows[vn], preds[base], preds[vn])
                    newerr[f"{vn}｜比｜{base}"] = {"old": old, "add": add}
        allres[n] = {"scores": {k: {kk: vv for kk, vv in r.items() if kk not in ("rows",)} for k, r in res.items()},
                     "feats": {i: {"rules": r, "f": f} for i, (r, f) in feats.items()}, "kept": kept, "jia_removed": jia_removed, "newerr": newerr}
        print(n, {k: (J.fmt(r["tol_in"]), J.fmt(r["strict"])) for k, r in res.items()}, flush=True)
    (OUTD / "任务2_缓解变体.json").write_text(json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
