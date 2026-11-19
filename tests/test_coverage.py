from baidu_ocr.coverage import check_coverage, iou, baidu_location_to_bbox, coverage_blocks_from_gaps


def test_iou_basic():
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0


def test_complete_coverage():
    expected = [
        {"id": "l1", "bbox": [10, 10, 100, 30]},
        {"id": "l2", "bbox": [10, 40, 100, 60]},
    ]
    words = [
        {"words": "一行", "location": {"left": 10, "top": 10, "width": 90, "height": 20}},
        {"words": "二行", "location": {"left": 10, "top": 40, "width": 90, "height": 20}},
    ]
    cov = check_coverage(expected, words, iou_threshold=0.3)
    assert cov["complete"] is True
    assert cov["missing"] == []


def test_missing_line_is_marked_not_silent():
    expected = [
        {"id": "l1", "bbox": [10, 10, 100, 30]},
        {"id": "l2", "bbox": [10, 40, 100, 60]},
    ]
    words = [
        {"words": "一行", "location": {"left": 10, "top": 10, "width": 90, "height": 20}},
    ]
    cov = check_coverage(expected, words, iou_threshold=0.3)
    assert cov["complete"] is False
    assert any(g["id"] == "l2" for g in cov["missing"])
    assert cov["gaps_marked"] is True
    blocks = coverage_blocks_from_gaps(cov["missing"], page=1, start_idx=1)
    assert blocks and blocks[0]["coverage_gap"] is True
    assert blocks[0]["text"] == ""
    assert "coverage_gap" in (blocks[0]["warnings"][0])


def test_empty_text_counts_as_gap():
    expected = [{"id": "l1", "bbox": [10, 10, 100, 30]}]
    words = [{"words": "  ", "location": {"left": 10, "top": 10, "width": 90, "height": 20}}]
    cov = check_coverage(expected, words)
    assert cov["complete"] is False
    assert cov["missing"][0]["reason"] == "matched_but_empty_text"


def test_location_to_bbox():
    assert baidu_location_to_bbox({"left": 5, "top": 7, "width": 10, "height": 3}) == [5, 7, 15, 10]
