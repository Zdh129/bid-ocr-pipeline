#!/usr/bin/env python3
"""混合路线准备（纯本地，不联网）：
1) 校验 frozen20/blind20-scan.pdf 的 SHA256；2) 200 DPI 渲染到 output/hybrid/renders/；3) 本地红章检测；
4) 按 rules.RULES 为路线 1 / 路线 2 选区域；5) 裁图（长边 ≤1600）到 output/hybrid/crops/route1|route2/；
6) 生成待发清单 output/hybrid/manifest_route1.json / manifest_route2.json 与预估 output/hybrid/estimate.json。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2  # noqa: E402

from baidu_ocr.hybrid import crops as C  # noqa: E402
from baidu_ocr.hybrid.inputs import PX_PER_PT, load_pages  # noqa: E402
from baidu_ocr.hybrid.rules import RULES, select_route1, select_route2  # noqa: E402

PDF = Path("/workspace/pdf-test/frozen20/blind20-scan.pdf")
PDF_SHA = "40fdaa2e4a49481365627d00f1105d25c3c4d47dfe2da147dbf45072fa34c9b5"
OUT = ROOT / "output" / "hybrid"
# 百度部分已花费（见 output/compare/report.md 第 3 节）
BAIDU_SPENT = {"B_standard": {"cny": 1.00, "sec": 47.8, "calls": "1 次整份提交（20 页计费）"},
               "A_accurate": {"cny": 1.12, "sec": 135.5, "calls": "28 次高精度含位置版"}}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out = Path(args.out)
    got = sha256(PDF)
    if got != PDF_SHA:
        raise SystemExit(f"frozen20 PDF 哈希不符：{got}")
    renders = C.render_pdf(PDF, out / "renders", dpi=RULES["render_dpi"])
    local_seals = {p: C.local_seal_boxes(path) for p, path in renders.items()}
    pages = load_pages(ROOT, local_seals)
    imgs = {}
    for pg in pages:
        img = cv2.imread(str(renders[pg["page"]]))
        if (img.shape[1], img.shape[0]) != (pg["w_px"], pg["h_px"]):
            raise SystemExit(f"p{pg['page']} 渲染尺寸 {img.shape[1]}x{img.shape[0]} 与 B 中间 JSON 记录 {pg['w_px']}x{pg['h_px']} 不一致")
        imgs[pg["page"]] = img

    estimate = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "timezone_note": "box 时钟为北京时间（CST, UTC+8）",
                "rules": RULES, "cny_per_usd": C.CNY_PER_USD, "prices_usd_per_1m": C.PRICES, "baidu_spent": BAIDU_SPENT, "routes": {}}
    for route, fn in (("route1", select_route1), ("route2", select_route2)):
        cdir = out / "crops" / route
        if cdir.exists():
            for f in cdir.glob("*.png"):
                f.unlink()
        items, stats_pages, skipped = [], {}, []
        for pg in pages:
            sel = fn(pg, PX_PER_PT)
            if "stats" in sel:
                stats_pages[pg["page"]] = sel["stats"]
            skipped += [{"page": pg["page"], "b_block_id": b["id"], "raw_type": b["raw_type"], "text": b.get("text")} for b in sel["hard"]["skipped"]]
            cnt = collections.Counter()
            for r in sel["regions"]:
                cnt[r["kind"]] += 1
                rid = f"{route[0]}{route[-1]}-p{pg['page']:02d}-{r['kind']}-{cnt[r['kind']]:02d}"
                r["id"] = rid
                r["route"] = route
                r["bbox_px"] = [round(v, 1) for v in C.bbox_pt_to_px(r["bbox_pt"], PX_PER_PT)]
                info = C.crop_region(imgs[pg["page"]], r["bbox_px"], r["kind"], cdir / f"{rid}.png")
                info["crop_path"] = str(Path(info["crop_path"]).relative_to(ROOT))
                r.update(info)
                pk, ptxt = C.prompt_for(r)
                r["prompt_kind"] = pk
                r.update(C.estimate(r, ptxt))
                items.append(r)
        by_kind = collections.Counter(i["kind"] for i in items)
        by_model = collections.Counter(i["model"] for i in items)
        reasons = collections.Counter(rr.split(":")[0] for i in items for rr in i["reasons"])
        usd = sum(i["est_usd"] for i in items)
        summ = {
            "n_blocks": len(items), "pages_involved": sorted({i["page"] for i in items}), "n_pages_involved": len({i["page"] for i in items}),
            "by_kind": dict(by_kind), "by_model": dict(by_model), "by_reason": dict(reasons),
            "by_page": dict(sorted(collections.Counter(i["page"] for i in items).items())),
            "est_prompt_tokens": sum(i["est_prompt_tokens"] for i in items), "est_completion_tokens": sum(i["est_completion_tokens"] for i in items),
            "est_usd": round(usd, 4), "est_cny": round(usd * C.CNY_PER_USD, 3),
            "est_usd_by_model": {m: round(sum(i["est_usd"] for i in items if i["model"] == m), 4) for m in by_model},
            "skipped_hand_sign": skipped,
            "max_crop_long_side": max(max(i["crop_w"], i["crop_h"]) for i in items) if items else 0,
        }
        if stats_pages:
            tot = collections.Counter()
            for s in stats_pages.values():
                tot.update(s)
            summ["cross_check"] = {"total": dict(tot), "by_page": stats_pages}
        man = {"route": route, "description": fn.__doc__.strip(), "source_pdf": str(PDF), "source_pdf_sha256": PDF_SHA,
               "render_dpi": RULES["render_dpi"], "rules_version": RULES["version"], "summary": summ, "items": items}
        (out / f"manifest_{route}.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
        estimate["routes"][route] = summ
        print(route, json.dumps({k: summ[k] for k in ("n_blocks", "n_pages_involved", "by_kind", "by_model", "est_usd", "est_cny")}, ensure_ascii=False))
        if "cross_check" in summ:
            print("  cross_check", summ["cross_check"]["total"])
    # 两路线去重后（同一裁图+同模型+同提示只调一次）的合并预估
    seen, dedup_usd, n_unique = set(), 0.0, 0
    for route in ("route1", "route2"):
        for i in json.loads((out / f"manifest_{route}.json").read_text(encoding="utf-8"))["items"]:
            key = (i["sha256"], i["model"], i["prompt_kind"])
            if key in seen:
                continue
            seen.add(key)
            n_unique += 1
            dedup_usd += i["est_usd"]
    estimate["both_routes_dedup"] = {"n_unique_calls": n_unique, "est_usd": round(dedup_usd, 4), "est_cny": round(dedup_usd * C.CNY_PER_USD, 3)}
    (out / "estimate.json").write_text(json.dumps(estimate, ensure_ascii=False, indent=2), encoding="utf-8")
    print("both_routes_dedup", estimate["both_routes_dedup"])


if __name__ == "__main__":
    main()
