import pytest
from fastapi import HTTPException

from backend.api.projects import OutlineItem, validate_outlines_payload


def test_validate_outlines_payload_rejects_empty_input():
    with pytest.raises(HTTPException, match="至少需要导入一章大纲"):
        validate_outlines_payload([])


def test_validate_outlines_payload_rejects_non_contiguous_chapter_numbers():
    payload = [
        OutlineItem(chapter_number=1, outline_text="第一章"),
        OutlineItem(chapter_number=3, outline_text="第三章"),
    ]

    with pytest.raises(HTTPException, match="章节号必须从 1 开始连续递增"):
        validate_outlines_payload(payload)


def test_validate_outlines_payload_rejects_duplicate_chapter_numbers():
    payload = [
        OutlineItem(chapter_number=1, outline_text="第一章"),
        OutlineItem(chapter_number=1, outline_text="重复第一章"),
    ]

    with pytest.raises(HTTPException, match="章节号不能重复: 1"):
        validate_outlines_payload(payload)


def test_validate_outlines_payload_rejects_too_many_chapters():
    payload = [
        OutlineItem(chapter_number=chapter_number, outline_text=f"第{chapter_number}章")
        for chapter_number in range(1, 502)
    ]

    with pytest.raises(HTTPException, match="一次最多导入 500 章大纲"):
        validate_outlines_payload(payload)
