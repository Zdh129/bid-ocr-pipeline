from baidu_ocr.client import BaiduOcrClient
from baidu_ocr.cost import CostTracker
from baidu_ocr.docparser import DocParserClient


def test_ocr_client_skips_network_when_disabled(tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # not a real image; should not be opened if skipped early
    # recognize_image 在 allow_external=false 且无 mock 时直接跳过，不读图
    cfg = {"allow_external": False, "cost": {"prices_cny": {"general": 0.01}, "cny_per_usd": 7.2}}
    client = BaiduOcrClient(cfg, CostTracker.from_config(cfg))
    out = client.recognize_image(img, "general", page=1, filename="x.pdf")
    assert out["dry_run"] is True
    assert out["skipped"] is True
    assert client.tracker.total_cny == 0


def test_ocr_client_uses_mock_without_network():
    cfg = {"allow_external": False, "cost": {"prices_cny": {"general": 0.01}, "cny_per_usd": 7.2}}
    client = BaiduOcrClient(cfg, CostTracker.from_config(cfg))
    mock = {"words_result": [{"words": "你好", "location": {"left": 1, "top": 1, "width": 10, "height": 10}}]}
    out = client.recognize_image("no-such.png", "general", mock_response=mock)
    assert out["ok"] is True and out["dry_run"] is True
    assert out["response"]["words_result"][0]["words"] == "你好"
    assert client.tracker.total_cny == 0


def test_docparser_skips_network(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cfg = {"allow_external": False, "docparser": {"edition": "standard"}, "cost": {"prices_cny": {"docparser_standard": 0.05}, "cny_per_usd": 7.2}}
    client = DocParserClient(cfg, CostTracker.from_config(cfg))
    out = client.parse_pdf(pdf, billed_pages=2)
    assert out["dry_run"] is True
    assert out["edition"] == "standard"
    assert "parser/task" in out["submit_url"]
    assert client.tracker.total_cny == 0


def test_cost_hard_cap():
    from baidu_ocr.cost import CostCapExceeded, CostTracker
    tr = CostTracker(cny_per_usd=1.0, soft_cap_usd=5, hard_cap_usd=10, prices_cny={"general": 9})
    tr.add(api="general", success=True, elapsed_sec=0.1)
    try:
        tr.add(api="general", success=True, elapsed_sec=0.1)
        raised = False
    except CostCapExceeded:
        raised = True
    assert raised
