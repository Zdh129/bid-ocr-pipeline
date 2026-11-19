"""20 页冻结集与 ChatGPT 参照的 SHA256 校验。文件不在时明确失败，不用替代品。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_frozen20_full(fcfg: dict, root: Path) -> dict:
    """新冻结集（2026-11-18 blind20）：逐文件完整 SHA256 校验，只返回允许外发的扫描 PDF。"""
    files = dict(fcfg.get("files") or {})
    result = {"dir": str(root), "exists": root.is_dir(), "ready": False, "mode": "full_sha256",
              "files": [], "errors": [], "scan_pdf": None, "reference": None, "manifest": None}
    if not root.is_dir():
        result["errors"].append("frozen20 目录不存在")
        return result
    for name, want in files.items():
        p = root / name
        if not p.is_file():
            result["errors"].append(f"缺文件 {name}")
            result["files"].append({"name": name, "present": False})
            continue
        got = sha256_file(p)
        ok = got.lower() == str(want).lower()
        result["files"].append({"name": name, "sha256": got, "expected": want, "ok": ok, "bytes": p.stat().st_size})
        if not ok:
            result["errors"].append(f"{name} SHA256 不符：{got}")
    ocr_input = fcfg.get("ocr_input")
    ref = fcfg.get("reference_file")
    if ocr_input not in files:
        result["errors"].append("ocr_input 必须在 files 校验清单里")
    if ref not in files:
        result["errors"].append("reference_file 必须在 files 校验清单里")
    if ocr_input in (fcfg.get("do_not_send") or []):
        result["errors"].append("ocr_input 在 do_not_send 名单里")
    mp = root / "manifest.json"
    if mp.exists():
        try:
            result["manifest"] = json.loads(mp.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"manifest.json 无法解析: {exc}")
    if not result["errors"]:
        result["scan_pdf"] = str(root / ocr_input)
        result["reference"] = str(root / ref)
        result["ready"] = True
    return result


def check_frozen20(cfg: dict | None = None, root: str | Path | None = None) -> dict:
    fcfg = (cfg or {}).get("frozen20") or {}
    root = Path(root or fcfg.get("dir") or "/workspace/pdf-test/frozen20")
    if fcfg.get("files"):
        return check_frozen20_full(fcfg, root)
    prefix = (fcfg.get("reference_sha256_prefix") or "da93f43f").lower()
    suffix = (fcfg.get("reference_sha256_suffix") or "b583").lower()
    result = {
        "dir": str(root),
        "exists": root.is_dir(),
        "ready": False,
        "pdfs": [],
        "references": [],
        "manifest": None,
        "errors": [],
        "expected_prefix": prefix,
        "expected_suffix": suffix,
        "matched_reference": None,
    }
    if not root.is_dir():
        result["errors"].append("frozen20 目录不存在")
        return result
    pdfs = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    refs = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in {".json", ".txt", ".md"} and p.name.lower() not in {"readme.md"}
    )
    manifest_path = root / "manifest.json"
    sums_path = root / "SHA256SUMS"
    if manifest_path.exists():
        try:
            result["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"manifest.json 无法解析: {exc}")
    for p in pdfs:
        result["pdfs"].append({"name": p.name, "sha256": sha256_file(p), "bytes": p.stat().st_size})
    matched = []
    for p in refs:
        digest = sha256_file(p)
        rec = {"name": str(p.relative_to(root)), "sha256": digest, "bytes": p.stat().st_size}
        rec["prefix_ok"] = digest.startswith(prefix)
        rec["suffix_ok"] = digest.endswith(suffix)
        result["references"].append(rec)
        if rec["prefix_ok"]:
            matched.append(rec)
            if suffix and not rec["suffix_ok"]:
                result["errors"].append(
                    f"参照 {rec['name']} 前缀匹配但后缀不是 {suffix}（完整哈希以官方记录为准）"
                )
    if matched:
        result["matched_reference"] = matched[0]
    if sums_path.exists():
        result["sha256sums_present"] = True

    if not pdfs:
        result["errors"].append("未找到冻结 PDF")
    if not matched:
        result["errors"].append(
            f"未找到 SHA256 以 {prefix} 开头的 ChatGPT 参照文件；禁止用文字层或其他输出替代"
        )
    result["ready"] = bool(pdfs and matched and not result["errors"])
    return result
