import yaml
from pathlib import Path

from baidu_ocr.hybrid import astra

ROOT = Path(__file__).resolve().parents[1]


def test_usd_batch_is_half_of_standard():
    s = astra.usd(1000, 500, 0, "standard")
    b = astra.usd(1000, 500, 0, "batch")
    assert abs(s - (1000 * 8 + 500 * 40) / 1e6) < 1e-9
    assert abs(b - s / 2) < 1e-9


def test_image_tokens_grows_with_size():
    assert astra.image_tokens(1600, 1600, "high") >= astra.image_tokens(200, 50, "high") > 0


def test_result_ok_rejects_length_and_empty():
    assert not astra.result_ok("line", "abc", "length")[0]
    assert not astra.result_ok("line", "", "stop")[0]
    assert astra.result_ok("line", "abc", "stop")[0]
    assert astra.result_ok("missing_line", "", "stop")[0]


def test_configs_guarded():
    live = yaml.safe_load(open(ROOT / "config_astra_live.yaml"))["vlm"]
    assert live["hard_cap_usd"] <= 5.0 and live["model"] == "gpt-6.2-astra"
    default = yaml.safe_load(open(ROOT / "config.yaml"))
    assert default.get("vlm", {}).get("allow_external", False) is False
