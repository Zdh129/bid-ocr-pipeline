"""页面类型标签（只打标签，不调接口、不改正文）。
classify() 及其辅助函数逐字摘自杨工脚本（附件 sha256 前缀 4f0b7506238b9b59，第 310–522 行「逐页关键词分流」一节），
只用于给已有识别文字打页面类型标签，写入逐页元数据；不按标签换接口、不改任何段落文字。
对外入口：tag_page(page_pred) -> {'页面类型','杨工接口','依据'}；tag_pred(pred) -> {页: 标签}。"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass, field
# 逐页关键词分流

@dataclass(frozen=True)
class Route:
    endpoint: str
    label: str
    reason: str
    options: dict[str, str] = field(default_factory=dict)


def _has(text: str, *keywords: str) -> bool:
    return any(keyword in text for keyword in keywords)


def _count(text: str, groups: tuple[tuple[str, ...], ...]) -> int:
    """Count distinct clues, without double-counting aliases of one field."""
    return sum(_has(text, *keywords) for keywords in groups)


def _financial_ticket(text: str) -> bool:
    """Require a ticket title and its fields, not a prose mention of expenses."""
    head = text[:200]
    if _has(head, "航空运输电子客票行程单", "航空电子客票行程单"):
        return _count(text, (("旅客姓名", "乘机人"), ("航班",),
                             ("电子客票号码", "票号"), ("票价", "合计"))) >= 2
    if _has(head, "铁路电子客票", "铁路车票", "中国铁路", "火车票"):
        return _count(text, (("车次",), ("发站", "出发站"), ("到站",),
                             ("票价", "二等座", "一等座", "硬座", "硬卧"))) >= 2
    if _has(head, "出租汽车专用发票", "出租车发票", "出租汽车发票"):
        return _count(text, (("发票号码", "发票代码"), ("车号", "车牌"),
                             ("里程",), ("金额", "合计"))) >= 2
    if _has(head, "高速公路通行费", "通行费发票", "通行费电子票据"):
        return _count(text, (("入口",), ("出口",), ("收费站",),
                             ("车牌", "车号"), ("金额", "合计"))) >= 2
    titles = ("定额发票", "通用机打发票", "机动车销售统一发票", "二手车销售统一发票")
    return _has(head, *titles) and _count(
        text, (("发票号码", "票据号码", "票据号"), ("发票代码", "票据代码"),
               ("开票日期", "交款人", "患者姓名"), ("金额", "合计", "价税合计")),
    ) >= 2


_TABLE_FIELDS = (
    ("投标人名称", "单位名称", "项目名称", "名称", "项目"),
    ("规格型号", "型号"), ("数量",), ("单价",), ("金额", "总价"), ("合计",),
    ("序号", "行次"), ("单位",), ("姓名",), ("身份证号", "身份证号码"),
    ("注册地址", "住所", "住址"), ("邮政编码", "邮政编"), ("联系人",),
    ("电话",), ("传真",), ("技术负责人",), ("员工总人数",),
    ("注册资金", "注册资本"), ("开户银行",), ("账号",),
    ("纳税人识别号", "统一社会信用代码"), ("税种",),
    ("税款所属时期", "所属期", "所属时期"), ("实缴", "应缴"),
    ("评价年度",), ("评价得分",), ("年度评价结果",),
    ("缴费基数",), ("缴费年月",), ("个人编号",),
    ("期末余额", "期末数"), ("期初余额", "期初数"),
    ("本期金额", "本年累计金额", "本年累计数", "本年数"), ("审定数",),
)
_TABLE_TITLES = (
    "基本情况表", "负责人信息表", "信用评价信息", "税收完税证明", "完税证明",
    "社会保险缴费明细", "社保缴费明细", "缴费明细表", "人员名单", "清单", "明细表", "库存名单",
    "资产负债表", "利润表", "现金流量表", "试算平衡表",
)


def _table_heading(text: str, normalized: str) -> bool:
    """Prefer short title lines; prose mentioning a financial report is not a title."""
    lines = ["".join(unicodedata.normalize("NFKC", line).split()).upper()
             for line in text.splitlines() if line.strip()]
    for line in lines[:16]:
        if len(line) > 60:
            continue
        for title in _TABLE_TITLES:
            prefix = line[:-len(title)]
            if title in {"资产负债表", "利润表", "现金流量表", "试算平衡表"} and prefix not in {
                "", "合并", "母公司", "公司", "企业",
            }:
                continue
            if line.endswith(title) and not _has(
                prefix, "本公司", "编制", "按照", "根据", "提供", "提交", "应当", "须",
            ):
                return True
    # A caller may supply a single already-joined OCR line. Require the title
    # near its beginning rather than searching arbitrary narrative paragraphs.
    return len(lines) == 1 and any(
        0 <= normalized.find(title) <= 10 and not _has(
            normalized[:normalized.find(title)],
            "本公司", "编制", "按照", "根据", "提供", "提交", "应当", "须",
        ) for title in _TABLE_TITLES
    )


def classify(text: str) -> list[Route]:
    """Return zero or one endpoint route using independently matched anchors.

    Invoice/card anchors take priority over table clues. Mere references to an
    invoice, ID photocopy or bank account in a contract are insufficient. Single
    ID faces use ``id_card_side=front/back``; mixed faces use ``multi_idcard``.
    A nonempty unmatched page uses ``accurate_basic`` for general text.
    """
    normalized = "".join(unicodedata.normalize("NFKC", text).split()).upper()
    if not normalized:
        return []
    head = normalized[:200]

    vat = (
        _has(head, "增值税专用发票", "增值税普通发票", "增值税电子普通发票",
             "增值税电子专用发票", "电子发票")
        and _has(normalized, "发票号码", "发票代码", "发票编号")
        and _has(normalized, "开票日期", "开票时间")
        and _count(normalized, (("购买方",), ("销售方",), ("价税合计",),
                               ("税额",), ("税率",), ("纳税人识别号",))) >= 2
    )
    if _has(head, "医疗门诊收费票据", "医疗住院收费票据", "财政票据", "非税收入一般缴款书",
            "非税收入缴款书", "非税缴款书") and _count(
        normalized, (("发票号码", "票据号码", "票据号"), ("发票代码", "票据代码"),
                     ("开票日期", "交款人", "患者姓名"), ("金额", "合计", "价税合计")),
    ) >= 2:
        return [Route("general_ocr", "医疗或财政票据", "医疗收费、财政或非税票据标题与多项票据字段匹配")]
    financial = _financial_ticket(normalized)
    multiple_vat = vat and normalized.count("发票号码") >= 2 and normalized.count("开票日期") >= 2
    if financial or multiple_vat:
        return [Route("multiple_invoice", "财务票据", "票据标题与多项财务字段匹配，或同页含多张发票")]
    if vat:
        return [Route("vat_invoice", "增值税发票", "增值税发票票头、号码、开票日期及购销或税额字段匹配")]

    id_front = (
        _has(normalized, "公民身份号码", "公民身份证号码")
        and _count(normalized, (("姓名",), ("性别",), ("民族",), ("出生",), ("住址",))) >= 3
    )
    id_back = (
        "居民身份证" in normalized
        and "中华人民共和国" in normalized
        and "签发机关" in normalized
        and _has(normalized, "有效期限", "有效期")
    )
    if id_front and id_back:
        return [Route("multi_idcard", "身份证双面", "同页同时出现身份证人像面及国徽面的独立字段组合")]
    if id_front:
        return [Route("idcard", "身份证人像面", "公民身份号码与多项人像面字段匹配", {"id_card_side": "front"})]
    if id_back:
        return [Route("idcard", "身份证国徽面", "居民身份证票头、签发机关和有效期限匹配", {"id_card_side": "back"})]

    if (
        re.search(r"营业执照(?!号)", head)
        and _count(normalized, (("统一社会信用代码", "注册号"), ("法定代表人", "负责人"),
                               ("注册资本",), ("成立日期",), ("经营范围",), ("住所",))) >= 3
    ):
        return [Route("business_license", "营业执照", "营业执照标题与三项以上登记字段匹配")]
    account_clues = _count(normalized, (
        ("核准号", "基本存款账户编号"), ("账号", "账户号码"),
        ("开户银行", "开户行"), ("存款人名称", "账户名称"),
    ))
    account_title = _has(head, "开户许可证", "基本存款账户信息") and account_clues >= 2
    account_approval = (
        account_clues >= 3
        and "核准号" in normalized
        and "基本存款账户" in normalized
        and _has(normalized, "准予开立", "批准开立", "符合开户条件")
    )
    if account_title or account_approval:
        return [Route("account_opening", "开户许可", "开户许可标题或核准号、基本存款账户审批语句及账户字段匹配")]
    if (
        _has(head, "借记卡", "储蓄卡", "贷记卡", "信用卡", "银行卡", "DEBIT", "CREDIT", "银联", "UNIONPAY")
        and _has(normalized, "银行", "BANK")
        and re.search(r"(?<!\d)\d{13,19}(?!\d)", normalized)
    ):
        return [Route("bankcard", "银行卡", "银行卡标志、银行名称及卡号数字串匹配")]
    if (
        _has(head, "护照", "PASSPORT")
        and _has(normalized, "护照号码", "护照号", "PASSPORTNO", "P<CHN")
        and _count(normalized, (("国籍", "NATIONALITY"), ("姓名", "SURNAME"),
                               ("签发日期", "DATEOFISSUE"), ("出生日期", "DATEOFBIRTH"))) >= 2
    ):
        return [Route("passport", "护照", "护照标题、号码或机读码及个人字段匹配")]
    if (
        "驾驶证" in head
        and "准驾车型" in normalized
        and _count(normalized, (("证号",), ("初次领证日期",), ("有效期限", "有效期"), ("姓名",))) >= 2
    ):
        return [Route("driving_license", "驾驶证", "驾驶证标题、准驾车型与领证或身份字段匹配")]
    if (
        "行驶证" in head
        and _count(normalized, (("号牌号码",), ("车辆类型",), ("所有人",),
                               ("车辆识别代号",), ("发动机号码",), ("使用性质",))) >= 3
    ):
        return [Route("vehicle_license", "行驶证", "行驶证标题与多项车辆登记字段匹配")]

    certificate_title = re.search(
        r"管理体系认证证书|职业资格证书|职业技能等级证书|专业技术资格证书|执业资格证书|职称证书|资质证书",
        head[:100],
    )
    certificate_heading = certificate_title and not _has(
        head[:certificate_title.start()], "提交", "提供", "要求", "应当", "须", "需", "复印件",
    )
    if certificate_heading and _count(normalized, (
        ("证书编号", "证书号码"), ("认证范围", "认证覆盖的范围"),
        ("发证日期", "签发日期", "颁发日期"), ("有效期",),
        ("持证人", "职业等级", "资格名称"),
    )) >= 2:
        return [Route("general_ocr", "通用证书", "认证或职业资格证书标题与编号、认证范围或发证有效期字段匹配")]

    table_clues = _count(normalized, _TABLE_FIELDS)
    staff_columns = (
        "岗位" in head and "职责" in head
        and _count(head, (("姓名", "负责人及单位"), ("联系电话",), ("投入时间",))) >= 2
    )
    inventory_columns = (
        _has(head, "库存名单", "库存清单") and "库存类别" in head
        and _has(normalized, "日均库存", "现货库存", "库存数量")
    )
    if ((_table_heading(text, normalized) and table_clues >= 2)
            or staff_columns or inventory_columns or table_clues >= 6):
        return [Route("table", "表格", "短表格标题、人员或库存表头与多项字段匹配，或存在六组以上不同表格字段")]
    return [Route("accurate_basic", "普通文字", "未匹配足够的票据、卡证或表格关键词组合")]



# ---- 以下为第二版新增：只读预测文字，输出元数据 ----
def page_text(pg: dict) -> str:
    return '\n'.join((s.get('text') or '') for s in pg.get('sections', []))
def tag_page(pg: dict) -> dict:
    r = classify(page_text(pg))
    if not r:
        return {'页面类型': '空白页', '杨工接口': None, '依据': '无识别文字'}
    return {'页面类型': r[0].label, '杨工接口': r[0].endpoint, '依据': r[0].reason}
def tag_pred(pred: dict) -> dict:
    return {p: tag_page(v) for p, v in pred.items()}
