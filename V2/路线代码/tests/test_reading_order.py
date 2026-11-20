from baidu_ocr.reading_order import assign_reading_order, sort_lines_reading_order


def test_top_to_bottom_then_left_to_right():
    blocks = [
        {"id": "b", "type": "paragraph", "bbox": [200, 100, 300, 120], "text": "右上"},
        {"id": "a", "type": "paragraph", "bbox": [10, 100, 110, 120], "text": "左上"},
        {"id": "c", "type": "paragraph", "bbox": [10, 200, 110, 220], "text": "左下"},
    ]
    out = assign_reading_order(blocks, page_width=400)
    texts = [b["text"] for b in sorted(out, key=lambda x: x["reading_order"])]
    assert texts == ["左上", "右上", "左下"]


def test_header_footer_not_in_middle():
    blocks = [
        {"id": "f", "type": "footer", "bbox": [10, 800, 300, 820], "text": "页脚"},
        {"id": "h", "type": "header", "bbox": [10, 10, 300, 30], "text": "页眉"},
        {"id": "p", "type": "paragraph", "bbox": [10, 100, 300, 200], "text": "正文"},
    ]
    out = assign_reading_order(blocks, page_width=400)
    texts = [b["text"] for b in sorted(out, key=lambda x: x["reading_order"])]
    assert texts[0] == "页眉"
    assert texts[-1] == "页脚"
    assert texts[1] == "正文"


def test_two_columns():
    # 左栏上下 + 右栏上下，宽度都小于 0.55 * page
    blocks = [
        {"id": "r1", "type": "paragraph", "bbox": [300, 50, 480, 80], "text": "右1"},
        {"id": "l2", "type": "paragraph", "bbox": [20, 150, 200, 180], "text": "左2"},
        {"id": "l1", "type": "paragraph", "bbox": [20, 50, 200, 80], "text": "左1"},
        {"id": "r2", "type": "paragraph", "bbox": [300, 150, 480, 180], "text": "右2"},
    ]
    out = assign_reading_order(blocks, page_width=500)
    texts = [b["text"] for b in sorted(out, key=lambda x: x["reading_order"])]
    assert texts == ["左1", "左2", "右1", "右2"]


def test_sort_baidu_lines():
    words = [
        {"words": "B", "location": {"left": 200, "top": 10, "width": 40, "height": 16}},
        {"words": "A", "location": {"left": 20, "top": 10, "width": 40, "height": 16}},
    ]
    ordered = sort_lines_reading_order(words, page_width=400)
    assert [w["words"] for w in ordered] == ["A", "B"]
