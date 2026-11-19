# PDF 百分百识别 · OCR 识别流水线

本仓库整理了当前最优识别路线的代码与各轮冻结规则，供领导在本地复测和后续整合。仓库内不含密钥、数据集、百度返回缓存或计分输出。

当前最优路线（尚未切换生产主路径）为：

1. **基线C**：百度文档解析 + 高精度含位置 + 方案E / 改进⊗ + 发票重排
2. **第1轮**：乙′ 版面改写（按格子重排）→ 合计行切分 → 表头定位 → 栏位纠正 → 竖排融合（最后一步）
3. **第2轮**：在第1轮上增加单列明细的合计切分、合计行纳入明细区下沿、表头带词表切分，以及栏位纠正的边界守卫

切换主路径的门槛不变：读出率须达到 99%、错字率须在 1% 以内，并至少用 20 张真实扫描发票加人工参照做一次验证。

## 环境依赖

- Python 3.10 或更高（整理时使用 3.13）
- 系统工具：`pdftoppm`（poppler-utils），表格线缓存缺失时由规则脚本在本地生成
- Python 包见 `requirements.txt`（按脚本实际 import 列出）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

包列表：`pymupdf`、`pillow`、`numpy`、`pyyaml`、`requests`、`jsonschema`、`pytest`、`opencv-python-headless`、`tiktoken`（仅部分对照/费用估算脚本用到，基线C 与第1、2 轮默认不需要联网功能）。

## 百度密钥（环境变量）

密钥只放在本机环境变量或未入库的 `.env` 里，不要写入代码、配置或日志。

```bash
cp .env.example .env   # 然后在本机编辑，不要提交 .env
export BAIDU_OCR_API_KEY='…'
export BAIDU_OCR_SECRET_KEY='…'
```

也可使用别名 `BAIDU_API_KEY`、`BAIDU_SECRET_KEY`（`python -m pipeline check-env` 会做映射）。检查是否已设置（只打印有/无，不打印值）：

```bash
python -m pipeline check-env
# 或
python scripts/check_env.py
```

根目录 `config.yaml` 默认 `allow_external: false`。要真正调用百度，必须：1）在专用配置里把 `allow_external` 改为 true；2）命令行同时加 `--allow-external`。二者缺一即按 dry-run 处理，不会取 token、不会计费。

## 数据集怎么放

见 `data/README.md`。约定：

```
data/<集名>/
  <集名>-scan.pdf
  blind-ref.txt
  blind-ref.json     # 如有
  manifest.json
```

仓库不附带 PDF 或参照。缺少人工参照时不要用文字层或其他输出冒充。

## 如何运行

先准备识别所需的百度输出（文档解析、高精度含位置、必要时的二次识别与页边竖排），再在本地套用各轮规则。下面入口默认不联网。

```bash
# 核对这些冻结文件是否与冻结记录一致（应全部通过）
python -m pipeline verify

# 由方案E预测生成基线C（方案E＋改进⊗）
python -m pipeline baseline-c --pred-c path/to/pred_方案E.json --out output/pred_C.json
# 或
python scripts/run_baseline_c.py --pred-c path/to/pred_方案E.json --out output/pred_C.json

# 第1轮冻结路线（需已有方案A字框、文档解析 JSON、渲染图、二次识别结果）
python -m pipeline round1 \
  --pred-c output/pred_C.json \
  --scheme-a-dir output/<集>/scheme_a \
  --pdf data/<集名>/<集名>-scan.pdf \
  --set-name <集名> \
  --doc-path output/<集>/scheme_b_standard/<stem>.json \
  --renders-dir output/<集>/scheme_a/renders \
  --reread-path output/<集>/reread/results.json \
  --out output/pred_round1.json

# 第2轮（参数相同）
python -m pipeline round2 --pred-c output/pred_C.json ... --out output/pred_round2.json
python scripts/run_round2.py --help
```

若要从 PDF 重新走百度调用（会产生费用）：

```bash
# 方案A：按页分流，正文走 general，表格/带章走 accurate（高精度含位置）
python scripts/run_scheme_a.py --config config.yaml --input data/<集名>/<集名>-scan.pdf --out output/<集>/scheme_a

# 方案B：整份提交文档解析（standard 或 unlimited）
python scripts/run_scheme_b.py --config config.yaml --input data/<集名>/<集名>-scan.pdf --out output/<集>/scheme_b_standard --edition standard

# 只有专用配置 allow_external: true 且同时加 --allow-external 才会真正发请求
# python scripts/run_scheme_b.py --config configs/config.live.example.yaml --allow-external ...
```

历史试验脚本（含原试验机路径，供对照，不作为推荐入口）在 `scripts/history/`。方案E 组合开关见 `config_方案E.yaml`。调用百度前可用 `scripts/ready_check_方案E.py` 对照冻结清单。

## 如何计分

- **主口径**：`scripts/tolerant_score.py`（容忍页边竖排、表头带配对、印章抽出等约定，不改参照文件）
- **严格口径**：`scripts/blind20_score.py` 中的 `score_system`（逐页拼接后做 Levenshtein 对齐）

```bash
python -m pipeline score \
  --pred output/pred_全部.json \
  --dataset data/<集名> \
  --out output/<集>/scores.json

python scoring/score_once.py --help
```

计分规则：**每个数据集只计一次**。脚本会在输出目录创建 `SCORED` 标记；已存在则拒绝再跑（确需覆盖须显式 `--force`，复测时不要用）。先生成全部预测并记录 sha，再打开参照计分。

底层方案输出也可用 `scripts/run_score.py`（中间 JSON 对参照）。单元测试与 dry-run：

```bash
python -m pytest
python scripts/run_dry_run.py --help
```

## 注意事项

- **不要修改生产环境已有的 150 DPI 默认配置。** 本测试流水线渲染默认为 200 DPI，只在隔离环境使用。
- 密钥只存本地，不外传，不写进日志或报告正文。
- 冻结目录 `rules/` 为只读快照，核对 sha256 后不要改文件。工作配置用根目录已去敏的 `config.yaml`。
- 若读出率达不到 99%、错字率达不到 1% 以内，不切换主路径。
- 在开发集上调参后，必须先冻结规则，再对新数据只计分一次。

## 最新成绩摘要（synth_blind_i 发票页，一次性盲测）

| 路线 | 错字数 / 读出率 / 错字率 |
|---|---|
| 基线C | 95 / 99.17% / 1.34% |
| 第1轮 | 84 / 99.45% / 1.18% |
| 第2轮 | 20 / 99.90% / 0.28% |

全套成绩：基线C 99 / 99.31% / 1.09%；第1轮 88 / 99.53% / 0.97%；第2轮 24 / 99.88% / 0.26%。第2轮尚未切换主路径。

## 目录结构

```
pipeline/          可移植入口与冻结规则加载（不改冻结文件）
rules/             冻结规则只读快照（baseline_c、round1、round2）
  baseline_c/      基线C 与乙′ 规则，含 基线C_方案E/ 完整代码快照
  round1/          第1轮冻结
  round2/          第2轮冻结
scoring/           一次性计分入口
scripts/           原流水线脚本、方案A/B 运行与计分
  history/         原试验入口脚本快照
  本地补测_1119/    与流水线相关的本地补测脚本
configs/           去敏后的工作配置与外发模板
src/baidu_ocr/     识别与后处理 Python 包
tests/             单元测试（使用 data/fixtures 下的模拟返回）
docs/              接口说明与去敏报告
data/              放置数据集（空，见 data/README.md）
output/            运行输出（默认忽略；仅保留冻结清单 JSON）
```
