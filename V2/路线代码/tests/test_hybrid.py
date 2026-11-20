"""混合路线（B 打底 + VLM）单测：规则、裁图、预估、dry-run 闸门、合并、★/☆ 规则。全部离线。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from baidu_ocr.hybrid import crops as C
from baidu_ocr.hybrid.merge import merge_route, parse_table, pred_pages_from_doc
from baidu_ocr.hybrid.rules import (RULES, invoice_noframe_rows, is_invoice_page, is_lowconf, merge_seal_regions,
                                    select_route1, select_route2, split_bands, table_row_mismatches, table_rows)
from baidu_ocr.hybrid.star_rule import apply_star_rule, apply_to_pred_pages
from baidu_ocr.hybrid.vlm import Runner, live_gate

ROOT = Path(__file__).resolve().parents[1]
PXPT = 200 / 72


# ----------------------------------------------------------------------------- ★/☆
@pytest.mark.parametrize("src,want,n", [
    ("☆3。使用温度范围", "★3.使用温度范围", 2),
    ("☆3.1符合标准", "★3.1符合标准", 1),
    ("0-500；☆4.重复性", "0-500；★4.重复性", 1),
    ("☆10。温度控制", "★10.温度控制", 2),
    ("☆ 5。测量", "★ 5.测量", 2),
    ("评分☆☆☆", "评分☆☆☆", 0),          # 不跟数字的 ☆ 不动
    ("合计。3", "合计。3", 0),              # 非 ★ 条款号后的句号不动
    ("★1。仪器", "★1.仪器", 1),
])
def test_star_rule(src, want, n):
    assert apply_star_rule(src) == (want, n)


def test_star_rule_leaves_seal():
    pp = {"1": {"sections": [{"section": "body", "text": "☆2。x"}], "seal": "☆1"}}
    out, hits = apply_to_pred_pages(pp)
    assert out["1"]["sections"][0]["text"] == "★2.x" and out["1"]["seal"] == "☆1" and hits == {"1": 2}
    assert pp["1"]["sections"][0]["text"] == "☆2。x"  # 不改原对象


# ----------------------------------------------------------------------------- 判定规则
def blk(id, typ="paragraph", raw="text", score=0.95, bbox=(10, 10, 100, 20), text="abc", **kw):
    return {"id": id, "type": typ, "raw_type": raw, "score": score, "bbox": list(bbox), "text": text, "reading_order": 0, **kw}


def test_lowconf_rule():
    assert is_lowconf(blk("a", score=0.94))
    assert not is_lowconf(blk("a", score=0.95))
    assert is_lowconf(blk("a", score=0.0))                   # 无分数也算
    assert is_lowconf(blk("a", raw="head_tail", score=0.5))
    assert not is_lowconf(blk("a", raw="hand_sign", score=0.1))  # 手写签名不送
    assert not is_lowconf(blk("a", typ="table", raw="table", score=0.0))
    assert not is_lowconf(blk("a", typ="seal", raw="seal", score=0.0))


def mk_table(rows_text, x0=50, y0=100, row_h=20, ncol=3, id="t1"):
    cells = []
    for r, rt in enumerate(rows_text):
        for c in range(ncol):
            cells.append({"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": rt[c] if c < len(rt) else "",
                          "bbox": [x0 + c * 100, y0 + r * row_h, x0 + (c + 1) * 100, y0 + (r + 1) * row_h]})
    return blk(id, typ="table", raw="table", score=0.0, bbox=(x0, y0, x0 + ncol * 100, y0 + len(rows_text) * row_h), text="",
               table={"n_rows": len(rows_text), "n_cols": ncol, "cells": cells})


def test_invoice_noframe_rows():
    t = mk_table([["购买方", "x", "y"], ["项目名称", "数量", "金额"], ["*仪器*A", "1", "9"], ["合计", "", "9"], ["价税合计", "", "9"]])
    assert is_invoice_page([blk("h", text="发票号码：123"), t])
    assert not is_invoice_page([blk("h", text="合同"), mk_table([["a", "b", "c"]])])
    assert invoice_noframe_rows(table_rows(t)) == [1, 2, 3]


def test_split_bands_forced_and_gap():
    t = mk_table([[str(i)] * 3 for i in range(6)])
    rows = table_rows(t)
    assert split_bands(rows, [0, 1, 2, 3, 4, 5], PXPT, {3}) == [[0, 1, 2], [3, 4, 5]]
    assert split_bands(rows, [0, 1, 4], PXPT) == [[0, 1], [4]]


def test_split_bands_size_limit():
    t = mk_table([[str(i)] * 3 for i in range(40)], row_h=30)  # 300pt×1200pt → 833×3333px，需要切
    rows = table_rows(t)
    bands = split_bands(rows, list(range(40)), PXPT)
    assert len(bands) > 1 and sum(len(b) for b in bands) == 40
    from baidu_ocr.hybrid.rules import band_ok
    for b in bands:
        assert band_ok(rows[b[0]]["x0"], rows[b[0]]["x1"], rows[b[0]]["band_y0"], rows[b[-1]]["band_y1"], PXPT) or len(b) == 1


def test_seal_merge():
    b1 = blk("s1", typ="seal", raw="seal", bbox=(100, 100, 150, 150))
    regs = merge_seal_regions([b1], [[105, 98, 155, 152], [400, 400, 450, 450]])
    assert len(regs) == 2
    assert regs[0]["b_ids"] == ["s1"] and len(regs[0]["local_boxes"]) == 1
    assert regs[1]["b_ids"] == [] and regs[1]["local_boxes"]


def page_fixture():
    t = mk_table([["a", "b", "c"], ["d", "e", "f"]], y0=200)
    blocks = [blk("b-001-001", text="甲方：某公司", score=0.95, bbox=(10, 10, 200, 22)),
              blk("b-001-002", text="低分行", score=0.4, bbox=(10, 30, 200, 42)),
              blk("b-001-003", text="正文一致", score=0.95, bbox=(10, 50, 200, 62)),
              blk("b-001-004", raw="hand_sign", text="张三", score=0.8, bbox=(10, 70, 60, 90)),
              t,
              blk("b-001-006", typ="seal", raw="seal", score=0.0, bbox=(300, 300, 340, 340), text="某公司合同专用章")]
    a_lines = [{"id": "a1", "bbox": [12, 11, 190, 21], "text": "甲方：某公可"},       # 与 B 不一致
               {"id": "a2", "bbox": [12, 31, 190, 41], "text": "低分行"},
               {"id": "a3", "bbox": [12, 51, 190, 61], "text": "正文一致"},
               {"id": "a4", "bbox": [12, 150, 190, 160], "text": "B漏掉的行"},       # B 无块覆盖 → 漏行
               {"id": "a5", "bbox": [305, 305, 335, 335], "text": "章"}]             # 落在印章内 → 不算漏行
    chars = []
    for r, row in enumerate([["a", "b", "c"], ["d", "X", "f"]]):        # 第 2 行中间格 A 读成 X
        for c, ch in enumerate(row):
            chars.append({"ch": ch, "bbox": [50 + c * 100 + 40, 200 + r * 20 + 5, 50 + c * 100 + 50, 200 + r * 20 + 15]})
    return {"page": 1, "b_blocks": blocks, "a_lines": a_lines, "a_gaps": [{"id": "g1", "bbox": [400, 600, 450, 610], "gap_class": "text_or_other"},
                                                                          {"id": "g2", "bbox": [400, 650, 450, 700], "gap_class": "qr_code"}],
            "a_chars": chars, "local_seals": [[302, 302, 338, 338]]}


def test_routes_share_rules_and_differ_in_policy():
    pg = page_fixture()
    r1 = select_route1(pg, PXPT)["regions"]
    s2 = select_route2(pg, PXPT)
    r2 = s2["regions"]
    k1 = {(r["kind"], json.dumps(r["target"], sort_keys=True)) for r in r1}
    # 两条路线：印章与低置信行判定一致
    for kind in ("seal",):
        assert {x for x in k1 if x[0] == kind} == {(r["kind"], json.dumps(r["target"], sort_keys=True)) for r in r2 if r["kind"] == kind}
    low1 = {r["target"]["b_block_id"] for r in r1 if r["kind"] == "line"}
    low2 = {r["target"]["b_block_id"] for r in r2 if r["kind"] == "line" and any(x.startswith("D3") for x in r["reasons"])}
    assert low1 == low2 == {"b-001-002"}  # 其余文字块 score=0.95，不算低置信
    # 路线 1：整表送；路线 2：只送不一致的第 2 行
    assert [r["target"]["row_idx"] for r in r1 if r["kind"] == "table_band"] == [[0, 1]]
    assert [r["target"]["row_idx"] for r in r2 if r["kind"] == "table_band"] == [[1, 1]]
    # 路线 2：A/B 不一致行、漏行、覆盖缺口
    mism = {r["target"]["b_block_id"] for r in r2 if r["kind"] == "line" and "ab_mismatch" in r["reasons"]}
    assert mism == {"b-001-001"}
    miss = [r for r in r2 if r["kind"] == "missing_line"]
    assert {r["target"].get("a_line_id") or r["target"].get("a_gap_id") for r in miss} == {"a4", "g1"}
    # 手写签名两条路线都不送
    assert all(r["target"].get("b_block_id") != "b-001-004" for r in r1 + r2)
    # 模型分配
    for r in r1 + r2:
        assert (r["model"], r["detail"]) == (("gpt-4o", "high") if r["kind"] == "table_band" else ("gpt-5-mini", "auto"))


def test_table_row_mismatch_cells():
    pg = page_fixture()
    t = pg["b_blocks"][4]
    m = table_row_mismatches(t, table_rows(t), pg["a_chars"])
    assert list(m) == [1] and m[1][0]["b"] == "e" and m[1][0]["a"] == "X"


# ----------------------------------------------------------------------------- 裁图与预估
def test_crop_downscale(tmp_path):
    img = np.full((2400, 3000, 3), 255, np.uint8)
    info = C.crop_region(img, [0, 0, 2990, 1000], "table_band", tmp_path / "x.png")
    assert max(info["crop_w"], info["crop_h"]) <= 1600 and info["scale"] < 1
    assert (tmp_path / "x.png").exists() and len(info["sha256"]) == 64


def test_token_estimate_matches_round2_calibration():
    # 第二轮实测：gpt-4o 1600×891 prompt_tokens=1293 ≈ 图像 1105 + 文本 ~188
    assert C.image_tokens("gpt-4o", 1600, 891, "high") == 1105
    assert C.image_tokens("gpt-4o", 1600, 1352, "high") == 765
    assert C.image_tokens("gpt-5-mini", 100, 100, "auto") >= 70
    assert C.usd_from_usage("gpt-4o", 1_000_000, 0) == 2.5
    assert C.usd_from_usage("gpt-5-mini", 0, 1_000_000) == 2.0


# ----------------------------------------------------------------------------- 外发闸门 / dry-run
class Boom(dict):
    def get(self, *a, **k):  # 任何对环境变量的读取都算失败
        raise AssertionError("dry-run / 开关未开时不得读取 key 环境变量")

    __getitem__ = get


def test_live_gate_defaults_refuse_without_touching_env():
    assert live_gate({"vlm": {"allow_external": False}}, False, Boom())
    assert live_gate({"vlm": {"allow_external": False}}, True, Boom())  # 开关没开：拒绝，且不看 env
    assert live_gate({"vlm": {"allow_external": True}}, True, {}) == ["环境变量 OPENAI_API_KEY 不存在（不打印值）"]
    assert live_gate({"vlm": {"allow_external": True}}, True, {"OPENAI_API_KEY": "sk-test"}) == []


def test_repo_configs_are_closed():
    for name in ("config_hybrid.yaml", "config.yaml"):
        cfg = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        assert (cfg.get("vlm") or cfg).get("allow_external") is False, name
    live = yaml.safe_load((ROOT / "config_hybrid_live.yaml").read_text(encoding="utf-8"))["vlm"]
    assert live["hard_cap_usd"] <= 2.0
    assert live.get("allow_external") is False
    # 生产配置不在本仓库。若本机仍有原路径则只读核对；没有则跳过。
    prod = Path("/workspace/pdf-test/ocr/config.yaml")
    if prod.exists():
        prod_txt = prod.read_text(encoding="utf-8")
        vals = []

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k == "allow_external":
                        vals.append(v)
                    walk(v)
        walk(yaml.safe_load(prod_txt))
        assert vals and all(v is False for v in vals)
        assert "生产默认 150 DPI" in prod_txt


def _item(tmp_path, kind="line", id="r1-p01-line-01", route="route1"):
    p = tmp_path / "crop.png"
    p.write_bytes(b"\x89PNG fake")
    return {"id": id, "route": route, "page": 1, "kind": kind, "reasons": ["D3"], "model": "gpt-5-mini", "detail": "auto",
            "crop_path": "crop.png", "sha256": "ab" * 32, "crop_w": 100, "crop_h": 30, "bbox_pt": [0, 0, 10, 10], "crop_box_px": [0, 0, 1, 1],
            "est_prompt_tokens": 300, "est_completion_tokens": 320, "est_usd": 0.0007, "target": {"b_block_id": "x"}, "context": {}}


def test_runner_dry_run_never_posts(tmp_path):
    def poster(*a, **k):
        raise AssertionError("dry-run 不得发请求")
    r = Runner(tmp_path, tmp_path, {"vlm": {"allow_external": True}}, live=False, poster=poster, environ=Boom())
    rec = r.run([_item(tmp_path)])[0]
    assert rec["dry_run"] and rec["est_usd"] == 0.0007


def test_runner_live_refuses_when_switch_off(tmp_path):
    with pytest.raises(PermissionError):
        Runner(tmp_path, tmp_path, {"vlm": {"allow_external": False}}, live=True, environ={"OPENAI_API_KEY": "sk-x"})


def test_runner_live_fake_poster_ledger_cache_and_caps(tmp_path):
    calls = []

    def poster(body, key, timeout):
        calls.append(body["model"])
        assert key == "sk-secret-123"
        return {"choices": [{"message": {"content": "识别结果"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100,
                          "completion_tokens_details": {"reasoning_tokens": 64}}}
    cfg = {"vlm": {"allow_external": True, "soft_cap_usd": 5, "hard_cap_usd": 10}}
    r = Runner(tmp_path, tmp_path, cfg, live=True, poster=poster, environ={"OPENAI_API_KEY": "sk-secret-123"})
    a = r.run_item(_item(tmp_path))
    b = r.run_item(_item(tmp_path, id="r2-p01-line-01", route="route2"))  # 同裁图同模型 → 复用
    assert a["ok"] and a["text"] == "识别结果" and a["usd"] == pytest.approx(0.00045)
    assert b["cache_hit"] and len(calls) == 1
    ledger = (tmp_path / "vlm" / "ledger.jsonl").read_text(encoding="utf-8")
    assert "sk-secret-123" not in ledger and "base64" not in ledger
    # 软上限：累计已到 $5 时停止
    r.spent = 5.0
    c = r.run_item(_item(tmp_path, id="r1-p01-line-09"), use_cache=False)
    assert not c["ok"] and "软上限" in c["skipped"]


# ----------------------------------------------------------------------------- 合并
def test_parse_table_md_and_html():
    md = "```markdown\n| 序号 | 名称 |\n| --- | --- |\n| 1 | 仪器 |\n```"
    assert parse_table(md) == [["序号", "名称"], ["1", "仪器"]]
    assert parse_table("<table><tr><td>a</td><td>b</td></tr></table>") == [["a", "b"]]


def _doc_from_page(pg):
    blocks = []
    for i, b in enumerate(pg["b_blocks"]):
        x = {k: v for k, v in b.items() if k not in ("raw_type", "score")}
        x.update({"page": 1, "reading_order": i, "bbox_unit": "pt", "verification_status": "auto_passed", "source": "ocr"})
        blocks.append(x)
    return {"pages": [{"page_number": 1}], "blocks": blocks, "assets": [], "warnings": []}


def test_merge_route2(tmp_path):
    pg = page_fixture()
    doc = _doc_from_page(pg)
    items = select_route2(pg, PXPT)["regions"]
    for k, it in enumerate(items):
        it.update({"id": f"r2-{k}", "route": "route2", "crop_path": f"c{k}.png", "sha256": "0" * 64, "crop_box_px": [0, 0, 1, 1],
                   "crop_w": 10, "crop_h": 10})
    res = {}
    for it in items:
        if it["kind"] == "table_band":
            res[it["id"]] = {"ok": True, "text": "| d | e | f |", "model": "gpt-4o"}
        elif it["kind"] == "seal":
            res[it["id"]] = {"ok": True, "text": "某公司合同专用章", "model": "gpt-5-mini"}
        elif it["kind"] == "missing_line":
            res[it["id"]] = {"ok": True, "text": "B漏掉的行" if it["target"].get("a_line_id") else "", "model": "gpt-5-mini"}
        else:
            res[it["id"]] = {"ok": True, "text": "甲方：某公司" if it["target"]["b_block_id"] == "b-001-001" else "低分行", "model": "gpt-5-mini"}
    out, pred, log = merge_route(doc, items, res)
    body = [s["text"] for s in pred["1"]["sections"] if s["section"] == "body"]
    assert "B漏掉的行" in body and body.index("B漏掉的行") > body.index("正文一致")  # 按 y 插入
    tbl = [s["text"] for s in pred["1"]["sections"] if s["section"] == "table"][0]
    assert tbl == "a b c\nd e f"
    assert pred["1"]["seal"] == "某公司合同专用章"
    changed = [b for b in out["blocks"] if b.get("vlm")]
    assert changed and all(b["vlm"]["crop"] and b.get("bbox") for b in changed if "region_id" in b["vlm"])
    assert doc["blocks"][0]["text"] == "甲方：某公司"  # 原 doc 不被修改


def test_merge_keeps_b_when_no_result():
    pg = page_fixture()
    doc = _doc_from_page(pg)
    items = select_route1(pg, PXPT)["regions"]
    for k, it in enumerate(items):
        it.update({"id": f"r1-{k}", "route": "route1"})
    out, pred, log = merge_route(doc, items, {})
    assert pred == pred_pages_from_doc(doc) and all(x["action"] == "kept_B" for x in log)


# ----------------------------------------------------------------------------- 已生成清单的一致性（若存在）
MAN = ROOT / "output" / "hybrid"


@pytest.mark.skipif(not (MAN / "manifest_route1.json").exists(), reason="尚未生成清单")
def test_generated_manifests_consistent():
    m1 = json.loads((MAN / "manifest_route1.json").read_text(encoding="utf-8"))
    m2 = json.loads((MAN / "manifest_route2.json").read_text(encoding="utf-8"))
    for m in (m1, m2):
        assert m["rules_version"] == RULES["version"] and m["render_dpi"] == 200
        for i in m["items"]:
            assert max(i["crop_w"], i["crop_h"]) <= 1600
            assert (ROOT / i["crop_path"]).exists()
            assert i["model"] == ("gpt-4o" if i["kind"] == "table_band" else "gpt-5-mini")
    seals1 = {(i["page"], tuple(i["bbox_pt"])) for i in m1["items"] if i["kind"] == "seal"}
    seals2 = {(i["page"], tuple(i["bbox_pt"])) for i in m2["items"] if i["kind"] == "seal"}
    assert seals1 == seals2
    low1 = {i["target"]["b_block_id"] for i in m1["items"] if i["kind"] == "line"}
    low2 = {i["target"]["b_block_id"] for i in m2["items"] if i["kind"] == "line" and any(r.startswith("D3") for r in i["reasons"])}
    assert low1 == low2


def test_parse_table_continuation_line():
    assert parse_table("| a | DMA4501 |\n+Alcolyzer |\n| b | c |") == [["a", "DMA4501+Alcolyzer"], ["b", "c"]]


def test_lowconf_offline_threshold():
    from baidu_ocr.hybrid.rules import lowconf_only_inactive
    it = {"kind": "line", "reasons": ["D3_lowconf:score=0.8"], "context": {"b_score": 0.8}}
    assert lowconf_only_inactive(it, 0.75) and not lowconf_only_inactive(it, 0.85) and not lowconf_only_inactive(it, 0.95)
    it0 = {"kind": "line", "reasons": ["D3_lowconf:score=0.0"], "context": {"b_score": 0.0}}
    assert not lowconf_only_inactive(it0, 0.75)                       # 无分数：任何阈值都采用
    itm = {"kind": "line", "reasons": ["ab_mismatch", "D3_lowconf:score=0.9"], "context": {"b_score": 0.9}}
    assert not lowconf_only_inactive(itm, 0.75)                       # 有 A/B 不一致：总是采用


def test_drop_bleed_rows():
    from baidu_ocr.hybrid.merge import drop_bleed_rows
    t = mk_table([["屏幕显示", "240×128"], ["操作键区", "23个按键"], ["菜单语言", "英语"], ["外壳", "钢"]], ncol=2)
    rows = table_rows(t)
    v = [["屏幕显示", "240×128"], ["操作键区", "23个按键"], ["菜单语言", "英语"], ["外壳", "钢"]]
    assert drop_bleed_rows(v, rows, 1, 2) == [["操作键区", "23个按键"], ["菜单语言", "英语"]]
    assert drop_bleed_rows(v, rows, 0, 3) == v


def test_ledger_spent_survives_restart(tmp_path):
    def poster(body, key, timeout):
        return {"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": 1000000, "completion_tokens": 0}}
    cfg = {"vlm": {"allow_external": True, "soft_cap_usd": 2, "hard_cap_usd": 2}}
    r = Runner(tmp_path, tmp_path, cfg, live=True, poster=poster, environ={"OPENAI_API_KEY": "k"})
    r.run_item(_item(tmp_path), use_cache=False)
    r2 = Runner(tmp_path, tmp_path, cfg, live=True, poster=poster, environ={"OPENAI_API_KEY": "k"})
    assert r2.spent == pytest.approx(0.25)  # 重启后累计费用从账本恢复，硬上限对多次运行累计生效
