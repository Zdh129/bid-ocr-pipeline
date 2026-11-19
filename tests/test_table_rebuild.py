from baidu_ocr.table_rebuild import rebuild_table_from_words, table_from_docparser


def test_rebuild_grid():
    words = []
    labels = [["A", "B"], ["C", "D"]]
    for r, row in enumerate(labels):
        for c, t in enumerate(row):
            words.append({"words": t, "location": {"left": c * 50, "top": r * 20, "width": 40, "height": 16}})
    table = rebuild_table_from_words(words, y_gap=10, x_gap=20)
    assert table["n_rows"] == 2 and table["n_cols"] == 2
    texts = {(c["row"], c["col"]): c["text"] for c in table["cells"]}
    assert texts[(0, 0)] == "A" and texts[(1, 1)] == "D"


def test_matrix_spans():
    raw = {
        "cells": [{"text": "合并表头"}, {"text": "备注"}, {"text": "A"}, {"text": "B"}, {"text": "C"}],
        "matrix": [[0, 0, 1], [2, 3, 4]],
    }
    table = table_from_docparser(raw)
    assert table["n_rows"] == 2 and table["n_cols"] == 3
    head = next(c for c in table["cells"] if c["text"] == "合并表头")
    assert head["colspan"] == 2 and head["row"] == 0 and head["col"] == 0
