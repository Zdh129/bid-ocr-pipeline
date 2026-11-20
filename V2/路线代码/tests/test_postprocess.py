"""postprocess（B 后处理正式步骤）单测：全部本地、合成数据为主，不联网。"""
import copy
from pathlib import Path

import pytest
import yaml

from baidu_ocr.postprocess import pred_pages, run_postprocess
from baidu_ocr.postprocess import fill_gaps, invoice_reorder, seal_strip, star
from baidu_ocr.postprocess.aux import is_qr_hallucination, load_u_items
from baidu_ocr.postprocess.textutil import insertions, norm_with_map

ROOT = Path(__file__).resolve().parents[1]


def _doc(blocks, n_pages=1):
    pages = [{"page_number": p, "width": 600.0, "height": 850.0} for p in range(1, n_pages + 1)]
    for i, b in enumerate(blocks):
        b.setdefault("reading_order", i)
        b.setdefault("verification_status", "auto_passed")
        b.setdefault("page", 1)
    return {"pages": pages, "blocks": blocks}


# ---------- star ----------
@pytest.mark.parametrize("src,exp", [
    ("☆3。使用温度范围", "★3.使用温度范围"),
    ("★12，波长", "★12.波长"),
    ("★3.1。输出", "★3.1.输出"),
    ("1。双层门结构", "1.双层门结构"),
    ("2。3", "2。3"),            # R3 只在后面不是数字时生效
    ("说明☆见附件", "说明☆见附件"),  # ☆ 后不是数字不改
    ("合同金额 100。", "合同金额 100。"),
])
def test_star_text(src, exp):
    assert star.apply_text(src)[0] == exp


def test_star_serial_dot_switch_and_seal_untouched():
    d = _doc([{"id": "b1", "type": "paragraph", "bbox": [0, 0, 10, 10], "text": "1。双层"},
              {"id": "s1", "type": "seal", "bbox": [0, 0, 10, 10], "text": "☆1。章"}])
    out, _ = run_postprocess(d, {"seal_strip": {"enabled": False}, "fill_gaps": {"enabled": False},
                                 "invoice_reorder": {"enabled": False}, "star": {"serial_dot": False}})
    assert out["blocks"][0]["text"] == "1。双层"
    assert out["blocks"][1]["text"] == "☆1。章"
    out2, info = run_postprocess(d, {"seal_strip": {"enabled": False}, "fill_gaps": {"enabled": False}, "invoice_reorder": {"enabled": False}})
    assert out2["blocks"][0]["text"] == "1.双层" and info["counts"]["star"] == 1
    assert d["blocks"][0]["text"] == "1。双层"  # 不改入参


# ---------- seal_strip ----------
def test_seal_strip_position_and_guards():
    seal = {"id": "s", "type": "seal", "bbox": [100, 100, 200, 200], "text": "贵州博凯科技开发有限公司"}
    blocks = [
        seal,
        {"id": "junk", "type": "paragraph", "bbox": [120, 120, 160, 135], "text": "同一"},
        {"id": "colon", "type": "paragraph", "bbox": [120, 140, 190, 155], "text": "型号：SPX"},
        {"id": "num", "type": "paragraph", "bbox": [120, 160, 190, 175], "text": "2.强制对流"},
        {"id": "list", "type": "list", "bbox": [120, 180, 190, 195], "text": "技术"},
        {"id": "far", "type": "paragraph", "bbox": [400, 400, 450, 420], "text": "同一"},
        {"id": "txt", "type": "paragraph", "bbox": [400, 500, 480, 520], "text": "博凯科技开发"},
        {"id": "kw", "type": "header", "bbox": [400, 600, 480, 620], "text": "贵州省税务局"},
    ]
    d = _doc(blocks)
    log = []
    n = seal_strip.apply(d, {"use_position": True, "use_text": True, "overlap_min": 0.6, "max_chars": 12,
                             "block_types": ["paragraph", "header"]}, log)
    st = {b["id"]: b["verification_status"] for b in d["blocks"]}
    assert st["junk"] == "rejected" and st["txt"] == "rejected" and st["kw"] == "rejected"
    assert st["colon"] != "rejected" and st["num"] != "rejected" and st["list"] != "rejected" and st["far"] != "rejected"
    assert n == 3
    pp = pred_pages(d)
    body = "".join(s["text"] for s in pp["1"]["sections"])
    assert "博凯科技开发" not in body and "型号" in body


# ---------- textutil / fill_gaps ----------
def test_insertions_global_and_free_ends():
    ins, lead, tail = insertions("ABCD", "AXBCD")
    assert ins == {1: "X"} and lead == tail == ""
    ins, lead, tail = insertions("BCD", "★BCD")
    assert ins == {0: "★"}
    ins, lead, tail = insertions("BCD", "zz★BCDyy", free_ends=True)
    assert ins == {} and lead == "zz★" and tail == "yy"


def test_norm_with_map():
    s, idx = norm_with_map("a b　ｃ")
    assert s == "abc" and idx == [0, 2, 4]


def test_fill_gaps_propose_rules():
    assert fill_gaps.propose("3.16仪器防护", "★3.16仪器防护", "前文★3.16 仪器防护后文", True, 2, 0.6) == [(0, "★")]
    assert fill_gaps.propose("名：微波消解仪", "又名：微波消解仪", "又名:微波消解仪", False, 2, 0.6) == [(0, "又")]
    # A、U 不一致：不补
    assert fill_gaps.propose("名：微波消解仪", "又名：微波消解仪", "名:微波消解仪", False, 2, 0.6) == []
    # 超过 max_len：不补
    assert fill_gaps.propose("微波消解仪", "微波消解仪器设备", "微波消解仪器设备", False, 2, 0.6) == []
    # ☆ 在 A/U 里先按 ★ 规则归一
    assert fill_gaps.propose("12波长", "☆12波长", "★12波长", False, 2, 0.6) == [(0, "★")]
    assert fill_gaps.insert_at("3.16 仪器", [(0, "★"), (4, "X")]) == "★3.16 X仪器"


def test_fill_gaps_apply_block_and_cell():
    d = _doc([{"id": "b", "type": "paragraph", "bbox": [100, 100, 200, 112], "text": "3.16仪器"},
              {"id": "t", "type": "table", "bbox": [0, 300, 300, 400], "text": "",
               "table": {"n_rows": 1, "n_cols": 1, "cells": [{"row": 0, "col": 0, "text": "名：仪", "bbox": [0, 300, 100, 320]}]}}])
    def ch(s, x0, y0, w=8):
        return [{"ch": c, "bbox": [x0 + i * w, y0, x0 + (i + 1) * w, y0 + 10]} for i, c in enumerate(s)]
    aux = {"pages": {1: {"a_chars": ch("★3.16仪器", 93, 101) + ch("又名：仪", 5, 305),
                         "u_items": [{"kind": "text", "bbox": [90, 100, 210, 112], "text": "★3.16仪器", "cells": []},
                                     {"kind": "table", "bbox": [0, 300, 300, 400], "text": "", "cells": ["又名:仪"]}]}}}
    log = []
    n = fill_gaps.apply(d, {"max_len": 2, "min_sim": 0.5, "a_pad_x": 10}, log, aux)
    assert d["blocks"][0]["text"] == "★3.16仪器"
    assert d["blocks"][1]["table"]["cells"][0]["text"] == "又名：仪"
    assert n == 2


# ---------- invoice_reorder ----------
def _invoice():
    head = [("项目名称", 0, 10, 100), ("规格型号", 1, 100, 160), ("金额", 2, 160, 240), ("税额", 3, 240, 320)]
    cells = [{"row": 0, "col": 0, "rowspan": 1, "colspan": 4, "text": "购买方信息", "bbox": [10, 50, 320, 90]}]
    cells += [{"row": 1, "col": c, "rowspan": 1, "colspan": 1, "text": t, "bbox": [x0, 90, x1, 100]} for t, c, x0, x1 in head]
    # B 把明细行和合计行并成一行
    row2 = [("*仪器*分析仪合计", 0, 10, 100), ("DS3F", 1, 100, 160), ("100.00\n¥200.00", 2, 160, 240), ("13.00\n¥26.00", 3, 240, 320)]
    cells += [{"row": 2, "col": c, "rowspan": 1, "colspan": 1, "text": t, "bbox": [x0, 100, x1, 150]} for t, c, x0, x1 in row2]
    cells += [{"row": 3, "col": 0, "rowspan": 1, "colspan": 4, "text": "价税合计（大写）", "bbox": [10, 150, 320, 170]}]
    tab = {"n_rows": 4, "n_cols": 4, "cells": cells}

    def ch(s, x0, y0, w=6):
        return [{"ch": c, "bbox": [x0 + i * w, y0, x0 + (i + 1) * w, y0 + 9]} for i, c in enumerate(s)]
    a = ch("*仪器*分析仪", 12, 105) + ch("DS3F", 102, 105) + ch("100.00", 165, 105) + ch("13.00", 245, 105) \
        + ch("合计", 30, 135) + ch("¥200.00", 165, 135) + ch("¥26.00", 245, 135)
    return tab, a


def test_invoice_reorder_splits_total_row():
    tab, a = _invoice()
    new, info = invoice_reorder.reorder_table(tab, a)
    rows = {}
    for c in new["cells"]:
        rows.setdefault(c["row"], []).append(c)
    txt = {r: " ".join(c["text"] for c in sorted(v, key=lambda c: c["col"])) for r, v in rows.items()}
    assert txt[2].split() == ["*仪器*分析仪", "DS3F", "100.00", "13.00"]
    assert txt[3].split() == ["合计", "¥200.00", "¥26.00"]
    assert txt[4] == "价税合计（大写）" and new["n_rows"] == 5
    assert info["new_rows"] == 2


def test_invoice_only_on_invoice_pages():
    tab, a = _invoice()
    blk = {"id": "t", "type": "table", "bbox": [0, 0, 400, 200], "text": "", "table": tab}
    d = _doc([{"id": "h", "type": "paragraph", "bbox": [0, 0, 10, 10], "text": "采购合同"}, blk])
    aux = {"pages": {1: {"a_chars": a, "u_items": []}}}
    assert invoice_reorder.apply(copy.deepcopy(d), {}, [], aux) == 0
    d["blocks"][0]["text"] = "电子发票（增值税专用发票）"
    assert invoice_reorder.apply(d, {}, [], aux) == 1


# ---------- aux / pipeline / config ----------
def test_qr_filter():
    assert is_qr_hallucination("The image provided is a QR code. It does not contain any text")
    assert is_qr_hallucination("[QR Code]")
    assert not is_qr_hallucination("NIRS DS 2500F 丹麦 FOSS")
    raw = {"pages": [{"page_num": 0, "meta": {"page_width": 595}, "layouts": [
        {"type": "header", "text": "[QR Code]", "position": [0, 0, 10, 10]},
        {"type": "text", "text": "正文", "position": [10, 10, 100, 10]}]}]}
    items, dropped = load_u_items(raw, {1: 597.96})
    assert dropped == 1 and len(items[1]) == 1 and abs(items[1][0]["bbox"][0] - 10 * 597.96 / 595) < 1e-6


def test_pipeline_switches_and_aux_required():
    d = _doc([{"id": "b", "type": "paragraph", "bbox": [0, 0, 10, 10], "text": "☆1。x"}])
    out, info = run_postprocess(d, {"enabled": False})
    assert out["blocks"][0]["text"] == "☆1。x" and info["counts"] == {}
    with pytest.raises(ValueError):
        run_postprocess(d, {})  # fill_gaps / invoice_reorder 默认开，需要 aux


def test_config_postprocess_section_and_closed():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["allow_external"] is False
    pp = cfg["postprocess"]
    assert pp["enabled"] and all(pp[k]["enabled"] for k in ("star", "seal_strip", "fill_gaps", "invoice_reorder"))
    assert 1 <= pp["fill_gaps"]["max_len"] <= 2
