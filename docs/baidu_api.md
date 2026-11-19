# 百度智能云 OCR / 文档解析接口备忘（2026-11 查阅）

查阅来源均为百度智能云官方文档。查不到的已标明「未确认」。单价为人民币，美元按配置里的估算汇率换算（默认 7.2，非正式牌价）。

## 鉴权与开通

- 方式：用「文字识别」应用的 **API Key + Secret Key**，换 `access_token`。
- 换票地址：`POST https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials&client_id={API Key}&client_secret={Secret Key}`
  文档：https://cloud.baidu.com/doc/AI_REFERENCE/s/um3zhy50e
- `access_token` 默认有效期 30 天（`expires_in`，秒）。
- 开通步骤：
  1. 注册百度智能云账号并完成实名认证；
  2. 进入[文字识别控制台](https://console.bce.baidu.com/ai/#/ai/ocr/overview/index) → 应用列表 → 创建应用，勾选文字识别相关接口；
  3. 在应用详情复制 API Key、Secret Key；
  4. 实名后进入控制台可自动领取免费测试额度；付费需开通按量后付费或购买次数包。
- 本仓库只从环境变量读取：`BAIDU_OCR_API_KEY`、`BAIDU_OCR_SECRET_KEY`，代码与日志不得打印其值，也不得打印 access_token。
- **是否同一个应用的 Key 可调用全部接口：** 三类接口的官方文档都使用同一套 `access_token` 机制（文字识别应用 AK/SK）。接口额度/开通是分开的。是否创建一个应用并勾选「文字识别」后即可同时调 general、accurate、两档文档解析，**未 100% 确认必须勾选哪些子接口**；若某接口返回鉴权或额度错误，需在控制台检查该接口是否已开通。

另有「API Key 鉴权」（Bearer）和云账号 AK/SK 签名两种机制，本测试默认只用 access_token。

## ① 通用文字识别（标准含位置版）general

- 文档：https://cloud.baidu.com/doc/OCR/s/vk3h7y58v （更新 2024-07-31）
- URL：`POST https://aip.baidubce.com/rest/2.0/ocr/v1/general?access_token=...`
- Header：`Content-Type: application/x-www-form-urlencoded`
- 输入：`image` / `url` / `pdf_file` / `ofd_file` 四选一（优先级 image > url > pdf_file > ofd_file）。本测试只传单页 PNG 的 `image`（base64）。
- 图像限制：base64+urlencode 后不超过 **8MB**，最短边 ≥15px，最长边 ≤**4096px**，jpg/jpeg/png/bmp。
- 常用可选参数：`language_type`（默认 CHN_ENG）、`detect_direction`、`paragraph`、`vertexes_location`、`probability`、`recognize_granularity`（big/small）。
- 返回：`words_result[]`，每项含 `words`、`location{left,top,width,height}`（像素，左上原点），可选 `probability`、`vertexes_location`。
- 免费额度：个人认证 **1000 次/月**，企业认证 **2000 次/月**（https://cloud.baidu.com/doc/OCR/s/9k3h7xuv6 ，更新 2026-09-23）。
- 按量后付费第一档（0–5 万次）：**0.0100 元/次**；更高档递减至 0.0047 元/次。仅成功调用计费。
- QPS：多数文字识别接口免费状态约 **2 QPS**、不保证并发；开通付费后保证 **10 QPS**（https://cloud.baidu.com/doc/OCR/s/skio8zez2）。general 是否有单独例外：**未确认，按上述通用规则计**。

## ② 通用文字识别（高精度含位置版）accurate

- 文档：https://cloud.baidu.com/doc/OCR/s/tk3h7y2aq （更新 2024-07-31）
- URL：`POST https://aip.baidubce.com/rest/2.0/ocr/v1/accurate?access_token=...`
- Header 与鉴权同 general。
- 图像限制：base64+urlencode 后不超过 **10MB**，最短边 ≥15px，最长边 ≤**8192px**。
- 参数大体同 general，另有更多语种、`char_probability`、`multidirectional_recognize` 等。
- 返回结构与 general 相同（`words_result` + `location`）。
- 免费额度：个人认证 **500 次/月**，企业认证 **1000 次/月**（https://cloud.baidu.com/doc/OCR/s/9k3h7xuv6 中「高精度含位置版」节）。
- 按量后付费第一档（0–5 万次）：**0.040 元/次**；更高档递减至 0.020 元/次。仅成功调用计费。
- QPS：同上，按通用规则 免费约 2、付费保证 10。是否有单独例外：**未确认**。

## ③ 文档解析（整份 PDF 异步）

官方目前至少有三档，名称不要混用：

### 标准版（文档解析 / 文档解析（标准版））

- 文档：https://cloud.baidu.com/doc/OCR/s/llxst5nn0 （标题即为「文档解析（标准版）」，更新 2026-11-16）
- 提交：`POST https://aip.baidubce.com/rest/2.0/brain/online/v2/parser/task?access_token=...`
- 查询：`POST https://aip.baidubce.com/rest/2.0/brain/online/v2/parser/task/query?access_token=...`，Body 传 `task_id`
- Header：`application/x-www-form-urlencoded`
- 必填：`file_name`；`file_data`（Base64）与 `file_url` 二选一。file_data 流式文档 ≤50MB，图片 ≤10MB；更大必须 file_url（PDF URL 最大 300MB、2000 页）。**测试环境不把样本传到公网，超 50MB 会拒绝提交。**
- 与本任务相关的可选参数：`page_filter`（页码从 0 开始）、`detect_seal`（默认 true）、`recognize_seal`（默认 true）、`html_table_format`、`angle_adjust` 等。
- 异步：提交后建议 5–10 秒轮询。`status`：pending / running / success / failed。成功后下载 `parse_result_url`（JSON）和可选 `markdown_url`，链接有效期 30 天。不要只凭 `error_code=0` 判断解析完成。
- QPS：提交 **2**，查询 **10**。
- 免费额度：个人 **200 页**，企业 **200 页**（https://cloud.baidu.com/doc/OCR/s/Fls06fa15）。
- 按量后付费：不限量 **原价 0.09 元/页，限时 0.05 元/页**（同上价格页，文档用划线价表示）。本仓库费用估算默认用限时价 0.05，并在报告中注明。
- 返回 JSON 含 `pages[]`（`page_num` 从 0 起）、`layouts[]`（`type` 含 title/text/table/seal 等，`position` 为 [x,y,w,h]）、`tables`、`meta.page_width/page_height`。坐标单位在不同示例中有的像 pt（约 595×841）、有的像像素，解析时按与 PDF 页尺寸的接近程度判断。

### Unlimited-OCR 版

- 文档：https://cloud.baidu.com/doc/OCR/s/fmr1p39gb （更新 2026-11-17）
- 提交：`POST https://aip.baidubce.com/rest/2.0/brain/online/v2/unlimited-ocr-parser/task?access_token=...`
- 查询：`POST https://aip.baidubce.com/rest/2.0/brain/online/v2/unlimited-ocr-parser/task/query?access_token=...`
- 与标准版 **不是同一个 URL**。同样用 access_token。PDF file_data 最大约 50MB / 500 页；file_url 最大 100MB / 500 页。
- QPS：提交 **2**，查询 **2**。
- 免费额度：已认证**新用户**个人/企业 **2000 页/月**（老用户是否自动享有：文档写可填问卷升级，**未在此确认老用户额度**）。
- 按量后付费：不限量 **原价 0.05 元/页，限时 0.02 元/页**。
- 返回同样有 `parse_result_url` / `markdown_url`；JSON 含 layouts（含 seal）、tables（`cells` + `matrix`，合并单元格在 matrix 中重复索引）。

### 文档解析（PaddleOCR-VL）

另有一档，价格与标准版资源包相近（限时 0.05 元/页，免费额度个人 200 / 企业 1000 页）。**本复测方案 B 不默认使用该档。** 接口路径以官方专页为准，此处未展开。

## 费用上限（本测试）

软上限 $5、硬上限 $10（按估算汇率把人民币换成美元）。硬上限触及后客户端拒绝继续计费调用。估算不默认扣减免费额度，以免低估。

20 页量级的粗算（按量第一档/限时价，不含重试）：

- 方案 A：若 10 次 general + 10 次 accurate ≈ 0.10 + 0.40 = **0.50 元**（约 $0.07）
- 方案 B 标准版：20 页 × 0.05 = **1.00 元**（约 $0.14）
- 方案 B Unlimited-OCR：20 页 × 0.02 = **0.40 元**（约 $0.06）

远低于 $5。若重试或重复跑，以实际 `cost.json` 为准。
