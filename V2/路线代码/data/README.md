# 数据集放置说明

本仓库不包含任何 PDF、扫描件、参照文本或百度返回缓存。请把要复测的集合放在本目录下。

## 目录约定

```
data/<集名>/
  <集名>-scan.pdf      # 扫描件（或待识别 PDF）
  blind-ref.txt        # 人工参照（主计分入口，文本）
  blind-ref.json       # 人工参照（结构化，若有）
  manifest.json        # 页码、版面类型（如 doc_type=发票）等元数据
```

示例：`data/synth18f/synth18f-scan.pdf`、`data/synth_blind_i/synth_blind_i-scan.pdf`、`data/real_scans/real_scans-scan.pdf`。

计分脚本（`scripts/synth18b_score.py` 的 `load_bs(<集目录>)`）按上述文件名读取参照。缺少参照时不要凭空生成，也不要用文字层冒充人工参照。

识别过程中的百度原始返回、中间 JSON、表格线缓存和计分结果写到仓库根目录的 `output/`（已被 .gitignore 排除），不要提交。

真实扫描发票建议至少 20 页，并配齐人工参照后再做主路径切换前的一次性计分。
