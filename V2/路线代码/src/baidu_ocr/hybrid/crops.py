"""渲染（200 DPI）、本地红章检测、裁图（长边 ≤1600）、提示词与 token/费用预估。只在本地运行。"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from .rules import RULES

# 美元 / 百万 token（与第二轮 ocr/src/pdf_ocr/round2/vlm_openai.py 一致）
PRICES = {
    "gpt-4o": {"input": 2.50, "output": 10.00, "cached": 1.25},
    "gpt-5-mini": {"input": 0.25, "output": 2.00, "cached": 0.025},
}
CNY_PER_USD = 7.2


def render_pdf(pdf: Path, out_dir: Path, dpi: int = 200) -> dict[int, Path]:
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    res = {}
    doc = pymupdf.open(str(pdf))
    try:
        for i, page in enumerate(doc, 1):
            path = out_dir / f"page-{i:04d}.png"
            if not path.exists():
                page.get_pixmap(dpi=dpi, alpha=False, annots=True).save(str(path))
            res[i] = path
    finally:
        doc.close()
    return res


def local_seal_boxes(img_path: Path) -> list[list[int]]:
    from ..blind20_local import load_bgr, seal_stats

    return seal_stats(load_bgr(str(img_path)))["boxes"]


def bbox_pt_to_px(b, px_per_pt: float) -> list[float]:
    return [v * px_per_pt for v in b]


def pad_for(kind: str) -> tuple[int, int]:
    p = RULES["pad_px"]
    if kind in ("line", "missing_line"):
        return p["line_x"], p["line_y"]
    if kind == "seal":
        return p["seal"], p["seal"]
    return p["table"], p["table"]


def crop_region(img, bbox_px, kind: str, dest: Path, max_side: int | None = None) -> dict:
    """img: BGR ndarray（整页渲染）。返回裁图信息；长边超过 max_side 时等比缩小。"""
    import cv2

    max_side = max_side or RULES["max_side_px"]
    H, W = img.shape[:2]
    px, py = pad_for(kind)
    x0 = int(max(0, math.floor(bbox_px[0] - px)))
    y0 = int(max(0, math.floor(bbox_px[1] - py)))
    x1 = int(min(W, math.ceil(bbox_px[2] + px)))
    y1 = int(min(H, math.ceil(bbox_px[3] + py)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError(f"裁剪框过小: {[x0, y0, x1, y1]}")
    crop = img[y0:y1, x0:x1]
    h, w = crop.shape[:2]
    scale = 1.0
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        crop = cv2.resize(crop, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dest), crop):
        raise IOError(f"写裁图失败: {dest}")
    data = dest.read_bytes()
    ch, cw = crop.shape[:2]
    return {"crop_path": str(dest), "crop_box_px": [x0, y0, x1, y1], "crop_w": int(cw), "crop_h": int(ch),
            "scale": round(scale, 6), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


# ----------------------------------------------------------------------------- 提示词
COMMON = ("你是文档识别助手。只转写这张裁剪图里可见的印刷文字，不翻译、不推测看不见的内容、不描述图片、不加任何前言后语。"
          "符号按图中实际形状输出（例如 ★ 与 ☆、。与 .、¥、%、±、≤、×、℃ 不要混用）。看不清的字写【?】。")


def prompt_for(region: dict) -> tuple[str, str]:
    """返回 (prompt_kind, prompt_text)。"""
    kind = region["kind"]
    if kind == "table_band":
        t = region["target"]
        invoice = any(r.startswith("D4_invoice") for r in region["reasons"])
        part = ("这是整张表格。" if t.get("is_full_table") else
                f"这是一张表格的一段（原表第 {t['rows'][0] + 1}–{t['rows'][1] + 1} 行，共 {t['n_rows_table']} 行；"
                + ("含表头）。" if t["rows"][0] == 0 else "不含表头，是续行，不要补写表头）。"))
        extra = ("这是发票明细区，可能没有框线：按列对齐关系逐行输出（项目名称、规格型号、单位、数量、单价、金额、税率/征收率、税额等），"
                 "同一明细行折行的文字要合并回同一个单元格。" if invoice else "")
        return ("invoice" if invoice else "table"), (
            COMMON + part + extra +
            "输出 Markdown 表格：每个可见行一行，单元格按从左到右顺序；合并单元格的文字只写一次（写在左上格，其余格留空）；"
            "单元格内换行直接连写。只输出本段范围内的行：裁图上下边缘若露出相邻行的局部，不要转写。忽略红色印章、手写签名和水印。只输出表格。")
    if kind == "seal":
        return "seal", ("你是文档识别助手。这是一枚印章（或骑缝章残片）的裁图。只输出印章上的文字："
                        "环形文字从起点顺时针连读为一行，中间横排文字另起一行；不要输出印章上的数字编码（防伪编号），"
                        "不要输出印章外的黑色正文。看不清的字写【?】。没有可辨认的印章文字就输出空。")
    return "line", (COMMON + "这是文档中一段文字的裁图，可能是一行，也可能是多行的一整段。按阅读顺序完整转写裁图内所有完整的文字行，"
                    "不要只挑其中一行；只有被裁切到、只露出一小部分笔画的上下边缘行才忽略。"
                    "忽略红色印章和水印；手写的人名签字忽略，但手写的日期、数字照常转写。只输出文字本身。")


# ----------------------------------------------------------------------------- token / 费用预估
def text_tokens(s: str) -> int:
    cjk = sum(1 for ch in s if ord(ch) > 0x2E7F)
    other = len(s) - cjk
    return int(math.ceil(cjk * 1.0 + other / 3.5))


def image_tokens(model: str, w: int, h: int, detail: str) -> int:
    """gpt-4o：85 + 170×tiles（先压进 2048×2048，再把短边压到 768，512 切片）。
    gpt-5-mini（detail=auto）：按第二轮实测拟合，≈ max(70, 1.5 × ceil(w/32)×ceil(h/32))，补丁数上限 1536（偏保守）。"""
    if model.startswith("gpt-4o"):
        if detail == "low":
            return 85
        s = min(1.0, 2048 / max(w, h))
        w2, h2 = w * s, h * s
        s = min(1.0, 768 / min(w2, h2))
        w2, h2 = w2 * s, h2 * s
        return 85 + 170 * math.ceil(w2 / 512) * math.ceil(h2 / 512)
    patches = math.ceil(w / 32) * math.ceil(h / 32)
    if patches > 1536:
        s = math.sqrt(1536 * 1024 / (w * h))
        patches = min(1536, math.ceil(w * s / 32) * math.ceil(h * s / 32))
    return int(max(70, math.ceil(1.5 * patches)))


def estimate(region: dict, prompt_text: str) -> dict:
    model = region["model"]
    w, h = region["crop_w"], region["crop_h"]
    p_tok = image_tokens(model, w, h, region["detail"]) + text_tokens(prompt_text) + 10
    exp = int(region["context"].get("expected_chars") or 0)
    if region["kind"] == "table_band":
        n_cells = int(region["context"].get("n_cells") or 0)
        c_tok = int(math.ceil(exp * 1.15 + n_cells * 2 + 20))
    else:
        c_tok = int(math.ceil(exp * 1.15 + 10))
    if model.startswith("gpt-5"):
        c_tok += 300  # reasoning_effort=low 的推理 token（第二轮中位数 256，取偏保守 300）
    pr = PRICES[model]
    usd = p_tok / 1e6 * pr["input"] + c_tok / 1e6 * pr["output"]
    return {"est_prompt_tokens": p_tok, "est_completion_tokens": c_tok, "est_usd": round(usd, 6)}


def usd_from_usage(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
    pr = PRICES.get(model) or PRICES["gpt-5-mini"]
    billed = max(0, prompt_tokens - cached_tokens)
    return round(billed / 1e6 * pr["input"] + cached_tokens / 1e6 * pr["cached"] + completion_tokens / 1e6 * pr["output"], 6)


def prompt_hash(prompt: str) -> str:
    import hashlib as _h
    return _h.sha256(prompt.encode("utf-8")).hexdigest()[:12]
