import copy

import pytest
import yaml
from pathlib import Path

from baidu_ocr.postprocess import run_postprocess
from baidu_ocr.postprocess import vlm_backfill as V
from baidu_ocr.postprocess.pipeline import DEFAULT_CFG

ROOT = Path(__file__).resolve().parents[1]


def _doc():
    return {"pages": [{"page_number": 1, "width": 600.0, "height": 800.0}],
            "blocks": [
                {"id": "b-001-001", "page": 1, "bbox": [100, 100, 300, 120], "type": "paragraph", "text": "甲方：某公司", "reading_order": 0},
                {"id": "b-001-002", "page": 1, "bbox": [100, 130, 300, 150], "type": "paragraph", "text": "签订日期：2024年7月30日", "reading_order": 1},
                {"id": "b-001-003", "page": 1, "bbox": [100, 300, 300, 320], "type": "paragraph", "text": "第三行", "reading_order": 2},
                {"id": "b-001-004", "page": 1, "bbox": [280, 400, 320, 440], "type": "paragraph", "text": "采购合", "reading_order": 3},
            ]}


def _item(iid, scen, bbox, target, ctx=None, kind="line"):
    return {"id": iid, "scenario": scen, "page": 1, "bbox_pt": bbox, "kind": kind, "target": target, "context": ctx or {},
            "crop_path": f"x/{iid}.png", "sha256": "0" * 64}


def _cfg(**kw):
    c = copy.deepcopy(DEFAULT_CFG)
    for k in ("star", "seal_strip", "fill_gaps", "invoice_reorder"):
        c[k]["enabled"] = False
    c["vlm_backfill"] = {**c["vlm_backfill"], "enabled": True, **kw}
    return c


def test_date_rule():
    assert V.is_date_field("签订日期：2024年5月6日") and V.is_date_field("日期: 2024 年 7 月 30 日")
    assert not V.is_date_field("开票日期：2024年10月31日") and not V.is_date_field("须于2024年8月24日前完成")
    bl = [{"id": "a", "page": 1, "type": "paragraph", "raw_type": "text", "text": "日期：2024年7月30日"},
          {"id": "s", "page": 1, "type": "paragraph", "raw_type": "hand_sign", "text": "周浪"},
          {"id": "c", "page": 2, "type": "paragraph", "raw_type": "text", "text": "日期：2024年7月30日"}]
    assert [b["id"] for b in V.handwritten_date_blocks(bl)] == ["a"]  # 第 2 页没有手写签名块


def test_guards():
    assert V.accept_missing("下载次数:1", "下纛：1", True)[0]
    assert not V.accept_missing("为", "0", True)[0]
    assert not V.accept_missing("回", "", False)[0]
    assert V.accept_missing("扫一扫", "", False)[0]
    assert not V.accept_missing("", "x", True)[0]
    assert V.is_margin_vertical([585, 80, 595, 130], 600) and not V.is_margin_vertical([280, 280, 330, 480], 600)


def test_default_off_and_no_external():
    assert DEFAULT_CFG["vlm_backfill"]["enabled"] is False
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["allow_external"] is False and cfg["postprocess"]["vlm_backfill"]["enabled"] is False
    d = _doc()
    out, info = run_postprocess(d, {k: {"enabled": False} for k in ("star", "seal_strip", "fill_gaps", "invoice_reorder")}, aux=None)
    assert [b["text"] for b in out["blocks"]] == [b["text"] for b in d["blocks"]]
    with pytest.raises(ValueError):  # 开了却没给结果：报错，而不是自己去外发
        run_postprocess(d, _cfg(), aux={"pages": {}})


def test_replace_insert_dedup_margin():
    items = [_item("d1", "handwritten_date", [100, 130, 300, 150], {"b_block_id": "b-001-002"}),
             _item("m1", "missing_line", [100, 200, 300, 220], {"a_line_id": "x"}, {"a_text": "第二行"}, "missing_line"),
             _item("m2", "missing_line", [285, 450, 320, 480], {"a_line_id": "y"}, {"a_text": "合"}, "missing_line"),
             _item("m3", "missing_line", [585, 80, 595, 130], {"a_line_id": "z"}, {"a_text": "下：1"}, "missing_line")]
    res = {"d1": {"ok": True, "text": "签订日期：2024年7月20日", "model": "m"}, "m1": {"ok": True, "text": "第二行", "model": "m"},
           "m2": {"ok": True, "text": "合", "model": "m"}, "m3": {"ok": True, "text": "下载次数:1", "model": "m"}}
    out, info = run_postprocess(_doc(), _cfg(), aux={"pages": {}, "vlm": {"items": items, "results": res}})
    txt = [b["text"] for b in sorted(out["blocks"], key=lambda b: b["reading_order"])]
    assert txt == ["甲方：某公司", "签订日期：2024年7月20日", "第二行", "第三行", "采购合", "下载次数:1"]
    acts = {e["item_id"]: e["action"] for e in info["log"] if e["step"] == "vlm_backfill"}
    assert acts == {"d1": "replace", "m1": "insert", "m2": "reject", "m3": "insert"}
    # 只开 (b)
    out2, _ = run_postprocess(_doc(), _cfg(missing_line=False), aux={"pages": {}, "vlm": {"items": items, "results": res}})
    assert len(out2["blocks"]) == 4 and out2["blocks"][1]["text"].endswith("20日")


def test_vlm_overrides_fill_gaps():
    d = _doc()
    log = [{"step": "fill_gaps", "page": 1, "block_id": "b-001-003", "before": "第三行", "after": "第三行补", "inserted": ["补"]}]
    d["blocks"][2]["text"] = "第三行补"
    items = [_item("m1", "missing_line", [100, 290, 300, 330], {"a_line_id": "x"}, {"a_text": "补字"}, "missing_line")]
    n = V.apply(d, {**V.DEFAULT, "enabled": True, "dedup_guard": False}, log, {"vlm": {"items": items, "results": {"m1": {"ok": True, "text": "补字", "model": "m"}}}})
    assert n == 1 and d["blocks"][2]["text"] == "第三行"
    assert log[-1]["reverted_fill_gaps"] == ["b-001-003"]
