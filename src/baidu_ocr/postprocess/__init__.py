"""B 标准版（文档解析）输出的正式后处理步骤（离线、纯本地，不联网）。

顺序：star（★/☆+序号点）→ seal_strip（剔除印章区域文字）→ fill_gaps（A+U 一致时补 B 漏字）→ invoice_reorder（发票按坐标重排）→ vlm_backfill（VLM 回填漏行/手写日期，默认关闭，只消费已有结果、不外发）。
每步都可在 config 的 postprocess 节单独开关；入口 run_postprocess()。
"""
from .pipeline import DEFAULT_CFG, pred_pages, run_postprocess  # noqa: F401
