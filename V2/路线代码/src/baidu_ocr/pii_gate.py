"""本地保守 PII 门禁。命中时只允许报告「文件名 + 页码 + 原因」，不得摘录正文。

逻辑与 /workspace/pdf-test/ocr/src/pdf_ocr/pii_gate.py 对齐，本包内自包含，不修改原文件。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ID18_RE = re.compile(r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]")
ID18_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
ID18_CHECK = "10X98765432"

KEYWORD_RE = re.compile(
    r"(公民身份号码|居民身份证|身份证号|身份号码|身份证|护照号|护照号码|护照|"
    r"驾驶证|驾驶执照|机动车驾驶证)"
)
COMBO = {
    "name": re.compile(r"姓名"),
    "sex": re.compile(r"性别"),
    "ethnicity": re.compile(r"民族"),
    "birth": re.compile(r"出生"),
}
FILENAME_HINT_RE = re.compile(
    r"(身份证|居民身份证|护照|驾驶证|驾驶执照|id[_\-]?card|passport|driver.?licen[sc]e)",
    re.I,
)


def valid_id18(s: str) -> bool:
    s = (s or "").upper()
    if len(s) != 18:
        return False
    try:
        total = sum(int(s[i]) * ID18_WEIGHTS[i] for i in range(17))
        return s[17] == ID18_CHECK[total % 11]
    except Exception:  # noqa: BLE001
        return False


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pii_reasons(
    text: str | None,
    *,
    filename: str | None = None,
    file_sha256: str | None = None,
    page: int | None = None,
    cfg: dict | None = None,
) -> list[str]:
    """返回命中原因列表；空列表表示放行。不返回任何正文片段。"""
    reasons: list[str] = []
    name = Path(filename).name if filename else ""
    if name and FILENAME_HINT_RE.search(name):
        reasons.append("filename_hint")
    pii_cfg = (cfg or {}).get("pii") or {}
    for fn in pii_cfg.get("blocklist_filenames") or []:
        if name and name == Path(fn).name:
            reasons.append("blocklist_filename")
            break
    for hx in pii_cfg.get("blocklist_sha256") or []:
        if file_sha256 and file_sha256.lower() == str(hx).lower():
            reasons.append("blocklist_sha256")
            break
    known_key = f"{name}:{int(page)}" if name and page is not None else None
    catalog = pii_cfg.get("_known_index")
    if catalog and known_key and known_key in catalog:
        reasons.append("known_pii_page")
    t = text or ""
    if t:
        for m in ID18_RE.findall(t):
            reasons.append("id18_checksum" if valid_id18(m) else "id18_pattern")
            break
        if KEYWORD_RE.search(t):
            reasons.append("id_or_passport_or_license_keyword")
        hits = sum(1 for rx in COMBO.values() if rx.search(t))
        if hits >= 3:
            reasons.append("name_sex_ethnicity_or_birth_combo")
    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def is_blocked(text: str | None, **kwargs) -> bool:
    return bool(pii_reasons(text, **kwargs))


def blocked_record(filename: str, page: int, reasons: list[str]) -> dict:
    return {"filename": Path(filename).name, "page": int(page), "pii": list(reasons)}


def load_known_pages(cfg: dict) -> dict[str, list[str]]:
    """加载已知 PII 页索引：{filename:page -> reasons}。不读正文。"""
    from .config import resolve_path

    pii_cfg = cfg.setdefault("pii", {})
    rel = pii_cfg.get("known_pages")
    index: dict[str, list[str]] = {}
    if not rel:
        pii_cfg["_known_index"] = index
        return index
    path = resolve_path(cfg, rel)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("pages") or data if isinstance(data, list) else data.get("pages") or []:
            if not isinstance(item, dict):
                continue
            fn = Path(item.get("filename") or "").name
            page = item.get("page")
            if not fn or page is None:
                continue
            index[f"{fn}:{int(page)}"] = list(item.get("pii") or item.get("reasons") or ["known_pii_page"])
    pii_cfg["_known_index"] = index
    return index
