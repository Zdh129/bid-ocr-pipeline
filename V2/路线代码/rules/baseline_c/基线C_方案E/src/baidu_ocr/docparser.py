"""方案 B：文档解析客户端（标准版 / Unlimited-OCR）。默认 dry-run，不发请求。"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from .auth import ExternalDisabled, TokenProvider
from .cost import CostCapExceeded, CostTracker

STANDARD_SUBMIT = "https://aip.baidubce.com/rest/2.0/brain/online/v2/parser/task"
STANDARD_QUERY = "https://aip.baidubce.com/rest/2.0/brain/online/v2/parser/task/query"
UNLIMITED_SUBMIT = "https://aip.baidubce.com/rest/2.0/brain/online/v2/unlimited-ocr-parser/task"
UNLIMITED_QUERY = "https://aip.baidubce.com/rest/2.0/brain/online/v2/unlimited-ocr-parser/task/query"

EDITIONS = {
    "standard": {
        "name": "文档解析（标准版）",
        "submit": STANDARD_SUBMIT,
        "query": STANDARD_QUERY,
        "price_key": "docparser_standard",
        "submit_qps": 2,
        "query_qps": 10,
    },
    "unlimited": {
        "name": "文档解析（Unlimited-OCR）",
        "submit": UNLIMITED_SUBMIT,
        "query": UNLIMITED_QUERY,
        "price_key": "docparser_unlimited",
        "submit_qps": 2,
        "query_qps": 2,
    },
}


class DocParserError(RuntimeError):
    pass


class DocParserClient:
    def __init__(self, cfg: dict, tracker: CostTracker | None = None, edition: str | None = None):
        self.cfg = cfg
        self.tracker = tracker or CostTracker.from_config(cfg)
        self.tokens = TokenProvider(cfg)
        ed = edition or ((cfg.get("docparser") or {}).get("edition") or "standard")
        if ed not in EDITIONS:
            raise DocParserError(f"未知文档解析版本: {ed}（可选 standard / unlimited）")
        self.edition = ed
        self.meta = EDITIONS[ed]

    def parse_pdf(
        self,
        pdf_path: str | Path,
        *,
        filename: str | None = None,
        page_filter: list[int] | None = None,
        mock_result: dict | None = None,
        billed_pages: int | None = None,
    ) -> dict[str, Any]:
        """提交整份 PDF 并轮询结果。page_filter 为 0-based（与官方一致）。

        allow_external=false 时不发请求；若提供 mock_result 则走解析逻辑。
        """
        pdf_path = Path(pdf_path)
        name = filename or pdf_path.name
        allow = bool(self.cfg.get("allow_external"))
        t0 = time.perf_counter()
        n_pages = billed_pages
        if n_pages is None:
            try:
                import pymupdf

                doc = pymupdf.open(pdf_path)
                try:
                    n_pages = int(doc.page_count)
                finally:
                    doc.close()
            except Exception:  # noqa: BLE001
                n_pages = 1
        if page_filter is not None:
            n_pages = len(page_filter)

        if not allow:
            elapsed = time.perf_counter() - t0
            self.tracker.add(
                api=self.meta["price_key"],
                units=n_pages,
                success=False,
                elapsed_sec=elapsed,
                filename=name,
                dry_run=True,
                extra={"edition": self.edition, "reason": "allow_external_false"},
            )
            return {
                "ok": bool(mock_result is not None),
                "dry_run": True,
                "edition": self.edition,
                "edition_name": self.meta["name"],
                "submit_url": self.meta["submit"],
                "query_url": self.meta["query"],
                "skipped": mock_result is None,
                "reason": None if mock_result is not None else "allow_external_false",
                "parse_result": mock_result,
                "elapsed_sec": elapsed,
                "billed_pages": n_pages,
            }

        ok_charge, _ = self.tracker.can_charge(self.meta["price_key"], n_pages)
        if not ok_charge:
            raise CostCapExceeded("费用硬上限，跳过文档解析提交")

        import requests

        token = self.tokens.get()
        with pdf_path.open("rb") as f:
            raw = f.read()
        # file_data 上限约 50M；更大需要 file_url，本测试不把样本传到公网
        if len(raw) > 50 * 1024 * 1024:
            raise DocParserError("PDF 超过 50MB，官方要求改用 file_url；测试环境不把样本上传到公网")
        b64 = base64.b64encode(raw).decode("ascii")
        dcfg = self.cfg.get("docparser") or {}
        body: dict[str, Any] = {"file_name": name, "file_data": b64}
        if self.edition == "standard":
            body["detect_seal"] = str(bool(dcfg.get("detect_seal", True))).capitalize()
            body["recognize_seal"] = str(bool(dcfg.get("recognize_seal", True))).capitalize()
        if page_filter is not None:
            # 官方为 list[int]，form 编码时以 JSON 字符串传递（文档示例为列表）
            import json

            body["page_filter"] = json.dumps(page_filter)
        timeout = float(dcfg.get("http_timeout_sec") or (self.cfg.get("auth") or {}).get("timeout_sec") or 60)
        submit = requests.post(
            self.meta["submit"] + "?access_token=" + token,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
        try:
            sdata = submit.json()
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - t0
            self.tracker.add(api=self.meta["price_key"], units=n_pages, success=False, elapsed_sec=elapsed, filename=name)
            raise DocParserError(f"提交响应不是 JSON，HTTP {submit.status_code}") from exc
        result = sdata.get("result") or {}
        task_id = result.get("task_id") if isinstance(result, dict) else None
        if not task_id:
            elapsed = time.perf_counter() - t0
            self.tracker.add(api=self.meta["price_key"], units=n_pages, success=False, elapsed_sec=elapsed, filename=name)
            return {
                "ok": False,
                "dry_run": False,
                "edition": self.edition,
                "error_code": sdata.get("error_code"),
                "error_msg": sdata.get("error_msg"),
                "elapsed_sec": elapsed,
            }

        interval = float(dcfg.get("poll_interval_sec") or 7)
        deadline = time.time() + float(dcfg.get("poll_timeout_sec") or 300)
        status = "pending"
        qdata: dict = {}
        while time.time() < deadline:
            time.sleep(interval)
            q = requests.post(
                self.meta["query"] + "?access_token=" + token,
                data={"task_id": task_id},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=timeout,
            )
            qdata = q.json()
            qres = qdata.get("result") or {}
            status = (qres.get("status") if isinstance(qres, dict) else None) or "unknown"
            if status == "success":
                parse_result = None
                markdown = None
                url = qres.get("parse_result_url")
                if url:
                    fetched = requests.get(url, timeout=timeout)
                    import json as _json
                    parse_result = _json.loads(fetched.content.decode("utf-8"))
                murl = qres.get("markdown_url")
                if murl:
                    try:
                        mf = requests.get(murl, timeout=timeout)
                        markdown = mf.content.decode("utf-8")
                    except Exception:  # noqa: BLE001
                        markdown = None
                elapsed = time.perf_counter() - t0
                self.tracker.add(api=self.meta["price_key"], units=n_pages, success=True, elapsed_sec=elapsed, filename=name,
                                 extra={"edition": self.edition})
                return {
                    "ok": True,
                    "dry_run": False,
                    "edition": self.edition,
                    "edition_name": self.meta["name"],
                    "task_id": task_id,
                    "status": status,
                    "markdown": markdown,
                    "parse_result": parse_result,
                    "submit_to_success_sec": round(time.perf_counter() - t0, 3),
                    "elapsed_sec": elapsed,
                    "billed_pages": n_pages,
                }
            if status == "failed":
                elapsed = time.perf_counter() - t0
                self.tracker.add(api=self.meta["price_key"], units=n_pages, success=False, elapsed_sec=elapsed, filename=name)
                return {
                    "ok": False,
                    "dry_run": False,
                    "edition": self.edition,
                    "task_id": task_id,
                    "status": status,
                    "task_error": qres.get("task_error"),
                    "elapsed_sec": elapsed,
                }
        elapsed = time.perf_counter() - t0
        self.tracker.add(api=self.meta["price_key"], units=n_pages, success=False, elapsed_sec=elapsed, filename=name)
        raise DocParserError(f"轮询超时，最后状态 {status}")
