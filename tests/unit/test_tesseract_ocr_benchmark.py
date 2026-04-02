from __future__ import annotations

from snapgit.ocr_benchmark.tesseract_benchmark import (
    aggregate_lines,
    parse_tesseract_tsv,
)


def test_parse_tesseract_tsv_skips_empty_words() -> None:
    tsv = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "5\t1\t1\t1\t1\t1\t10\t20\t50\t10\t95.5\tHello",
            "5\t1\t1\t1\t1\t2\t70\t20\t50\t10\t-1\t",
            "5\t1\t1\t1\t1\t3\t130\t20\t50\t10\t88.0\tworld",
        ]
    )
    words = parse_tesseract_tsv(tsv)

    assert len(words) == 2
    assert words[0]["text"] == "Hello"
    assert words[1]["text"] == "world"


def test_aggregate_lines_groups_words_by_line() -> None:
    words = [
        {
            "block_num": 1,
            "par_num": 1,
            "line_num": 1,
            "text": "Ala",
            "conf": 90.0,
            "bbox": {"left": 10, "top": 10, "width": 20, "height": 10},
        },
        {
            "block_num": 1,
            "par_num": 1,
            "line_num": 1,
            "text": "ma",
            "conf": 80.0,
            "bbox": {"left": 35, "top": 10, "width": 20, "height": 10},
        },
        {
            "block_num": 1,
            "par_num": 1,
            "line_num": 2,
            "text": "kota",
            "conf": 70.0,
            "bbox": {"left": 10, "top": 30, "width": 30, "height": 10},
        },
    ]
    lines = aggregate_lines(words)

    assert [line["text"] for line in lines] == ["Ala ma", "kota"]
    assert lines[0]["confidence"] == 85.0
    assert lines[1]["confidence"] == 70.0
