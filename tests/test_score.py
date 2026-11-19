"""计分器单元测试：手写小例子。"""
from baidu_ocr.score import levenshtein_alignment, normalize, score_text


def test_normalize_nfkc_and_whitespace_keep_punct():
    s = "ＡＢＣ　123，你好\n！"
    assert normalize(s) == "ABC123,你好!"


def test_exclude_uncertain_token():
    ref = "合同编号【?】甲字第1号"
    assert normalize(ref) == "合同编号甲字第1号"
    # 计分时不确定字符不要求读出
    m = score_text("合同编号甲字第1号", ref)
    assert m["deletions"] == 0
    assert m["readout"] == 1.0
    assert m["cer"] == 0.0


def test_exact_match():
    m = score_text("你好，世界。", "你好，世界。")
    assert m["matches"] == m["ref_chars"]
    assert m["deletions"] == 0
    assert m["substitutions"] == 0
    assert m["insertions"] == 0
    assert m["readout"] == 1.0
    assert m["cer"] == 0.0


def test_deletion_is_missed_char():
    # 参照「ABCDEF」，预测漏了 C
    m = score_text("ABDEF", "ABCDEF")
    assert m["deletions"] == 1
    assert m["substitutions"] == 0
    assert m["insertions"] == 0
    assert m["matches"] == 5
    assert m["readout"] == round(5 / 6, 6)
    assert m["cer"] == round(1 / 6, 6)


def test_substitution():
    m = score_text("你好，世介。", "你好，世界。")
    assert m["substitutions"] == 1
    assert m["deletions"] == 0
    assert m["insertions"] == 0
    assert m["readout"] == round(5 / 6, 6)  # 6 个字符（含标点）里 5 个正确
    assert m["cer"] == round(1 / 6, 6)


def test_insertion():
    m = score_text("你好，世界啊。", "你好，世界。")
    assert m["insertions"] == 1
    assert m["deletions"] == 0
    assert m["substitutions"] == 0
    assert m["readout"] == 1.0  # 参照字符全部读出
    assert m["cer"] == round(1 / 6, 6)


def test_mixed_and_ignore_spaces():
    pred = "招标 编号：ZB-2026-001"
    ref = "招标编号：ZB-2026-001"
    m = score_text(pred, ref)
    assert m["cer"] == 0.0
    assert m["readout"] == 1.0


def test_alignment_ops_sum():
    aln = levenshtein_alignment("AC", "ABC")
    assert aln["deletions"] == 1
    assert aln["matches"] == 2
    assert aln["edit_distance"] == 1
    assert aln["matches"] + aln["substitutions"] + aln["deletions"] == aln["ref_chars"]


def test_empty_reference():
    m = score_text("", "")
    assert m["note"] == "empty_reference_after_normalize"
    assert m["readout"] == 1.0
    m2 = score_text("有字", "")
    assert m2["cer"] == 1.0
