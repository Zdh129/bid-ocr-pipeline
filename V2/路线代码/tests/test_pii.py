from baidu_ocr.pii_gate import pii_reasons, valid_id18


def test_valid_id18_checksum():
    # 公开示例：110101199003078932（校验位按国标计算，这里用函数自洽生成）
    # 构造：前 17 位 11010119900307893，计算校验位
    base = "11010119900307893"
    from baidu_ocr.pii_gate import ID18_WEIGHTS, ID18_CHECK
    total = sum(int(base[i]) * ID18_WEIGHTS[i] for i in range(17))
    full = base + ID18_CHECK[total % 11]
    assert valid_id18(full)
    reasons = pii_reasons(f"号码{full}")
    assert "id18_checksum" in reasons


def test_invalid_checksum_still_pattern():
    reasons = pii_reasons("110101199003078930")  # 末位故意错误但格式像身份证
    assert "id18_pattern" in reasons or "id18_checksum" in reasons


def test_keyword():
    assert "id_or_passport_or_license_keyword" in pii_reasons("请出示居民身份证原件")


def test_combo_layout():
    reasons = pii_reasons("姓名 性别 民族 某某")
    assert "name_sex_ethnicity_or_birth_combo" in reasons


def test_clean_text_passes():
    assert pii_reasons("某某市政务云扩容项目招标文件，招标编号 ZB-2026-001") == []


def test_blocked_record_has_no_body():
    from baidu_ocr.pii_gate import blocked_record
    rec = blocked_record("/tmp/foo.pdf", 3, ["id18_checksum"])
    assert rec == {"filename": "foo.pdf", "page": 3, "pii": ["id18_checksum"]}
    assert "text" not in rec


def test_filename_blocklist():
    cfg = {"pii": {"blocklist_filenames": ["secret.png"]}}
    reasons = pii_reasons("", filename="secret.png", cfg=cfg)
    assert "blocklist_filename" in reasons
