#!/usr/bin/env python3
"""预处理变体本地可行性粗测：本地 PaddleOCR（ocr 项目隔离 venv、本地缓存模型 PP-OCRv6_medium，经 `unshare -rn` 断网运行）。
指标（按页，字袋口径，不看读序）：正文召回 = 参照正文字（不含【印章】行）被读出的比例；多读 = 输出里超出参照正文字袋的字数（印章混入、杂字）；
关键探针：发票页竖排「下载次数」、身份证页「公民身份号码」与 18 位号码、签署栏被章压住的字。
用法：unshare -rn /workspace/pdf-test/ocr/.venv/bin/python scripts/synth18_preproc_localocr.py
"""
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

os.environ.setdefault("PADDLE_PDX_CACHE_HOME", "/workspace/pdf-test/ocr/.cache/paddlex")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("OMP_NUM_THREADS", "8")

ROOT = Path(__file__).resolve().parents[1]
PRE = ROOT / "output" / "synth18_opt" / "preproc"
OUT = ROOT / "output" / "synth18_opt" / "localocr"
REF = json.loads(Path("/workspace/pdf-test/synth18/blind-ref.json").read_text(encoding="utf-8"))
PAGES = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else "2,4,6,11,15,16,18".split(","))]
VARS = ["orig", "P1", "P2", "P3", "P2_P3", "P1_P2", "P1_P3", "P1_P2_P3"]
PROBES = {"下载次数": "发票竖排小字", "公民身份号码": "身份证字段", "990102198607120046": "身份证号", "法定代表人": "签署栏", "有限公司": "单位名"}


def norm(s):
    s = unicodedata.normalize("NFKC", s)
    return "".join(c for c in s if not c.isspace() and not unicodedata.category(c).startswith("P"))


def main():
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(lang="ch", text_detection_model_name="PP-OCRv6_medium_det", text_recognition_model_name="PP-OCRv6_medium_rec",
                    use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, device="cpu", text_det_limit_side_len=1920, text_det_limit_type="max",
                    enable_mkldnn=False, cpu_threads=8)
    OUT.mkdir(parents=True, exist_ok=True)
    res = json.loads((OUT / "results.json").read_text()) if (OUT / "results.json").exists() else {}
    for p in PAGES:
        pg = next(x for x in REF["pages"] if x["page"] == p)
        body = norm("".join(l["text"] for l in pg["ref_lines"] if not l["text"].startswith("【")))
        rb = Counter(body)
        for v in VARS:
            key = f"p{p:02d}/{v}"
            if key in res:
                continue
            t0 = time.time()
            r = ocr.predict(str(PRE / v / f"p{p:02d}.png"))[0]
            txt = "\n".join(r["rec_texts"])
            n = norm(txt)
            ob = Counter(n)
            hit = sum((rb & ob).values())
            res[key] = {"recall": round(hit / max(1, sum(rb.values())), 4), "extra": sum((ob - rb).values()), "chars": len(n),
                        "probes": {k: (k in n) for k in PROBES if k in body}, "sec": round(time.time() - t0, 1)}
            (OUT / f"p{p:02d}_{v}.txt").write_text(txt, encoding="utf-8")
            (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            print(key, res[key], flush=True)


if __name__ == "__main__":
    main()
