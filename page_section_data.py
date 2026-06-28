"""Wiki 页面「条件」「内容」章节解析（生物群系 / 事件共用）。"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag

try:
    from .prepare_data import (
        _append_description_list,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        _parse_description_list_block,
        _parse_description_paragraph_rich,
        _parse_tag_rich,
        _rich_segments_to_text,
    )
except ImportError:
    from prepare_data import (
        _append_description_list,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        _parse_description_list_block,
        _parse_description_paragraph_rich,
        _parse_tag_rich,
        _rich_segments_to_text,
    )


def find_section_h2(soup: BeautifulSoup, section_prefix: str) -> Tag | None:
    for h2 in soup.find_all("h2"):
        headline = h2.find(class_="mw-headline")
        if headline and headline.get("id", "").startswith(section_prefix):
            return h2
    return None


def _append_rich_paragraph(
    paragraphs: list[str],
    rich_paragraphs: list[list[dict]],
    rich: list[dict],
) -> None:
    text = _rich_segments_to_text(rich)
    if text:
        paragraphs.append(text)
        rich_paragraphs.append(rich)


def parse_conditions_section(soup: BeautifulSoup) -> dict[str, Any] | None:
    """解析 h2#条件 至下一 h2 之间的段落与列表。"""
    h2 = find_section_h2(soup, "条件")
    if not h2:
        return None

    paragraphs: list[str] = []
    rich_paragraphs: list[list[dict]] = []

    for sib in h2.find_next_siblings():
        if not isinstance(sib, Tag):
            continue
        if sib.name == "h2":
            break
        if sib.name == "p":
            _append_rich_paragraph(
                paragraphs,
                rich_paragraphs,
                _parse_description_paragraph_rich(sib),
            )
            continue
        if sib.name in ("ul", "ol"):
            parsed_list = _parse_description_list_block(sib)
            if parsed_list:
                text, rich = parsed_list
                _append_description_list(paragraphs, rich_paragraphs, text, rich)
            continue

    if not paragraphs:
        return None
    return {"text": "\n\n".join(paragraphs), "rich": rich_paragraphs}


def _parse_dotlist_item(span_i: Tag) -> dict[str, str] | None:
    link = span_i.select_one("a[title]")
    name = _clean_text(link.get("title", "")) if link else ""
    if not name:
        return None

    entry: dict[str, str] = {"name": name}
    img = span_i.select_one("img")
    if img and img.get("src"):
        entry["image"] = _filename_from_url(_image_url_from_src(img["src"]))

    note_parts: list[str] = []
    for note_span in span_i.select("span.note2, span.note"):
        note = _clean_text(note_span.get_text())
        if note:
            note_parts.append(note)
    if note_parts:
        entry["note"] = "".join(note_parts)
        entry["label"] = name + entry["note"]
    else:
        entry["label"] = name
    return entry


def _parse_box_items(content_el: Tag | None) -> list[dict[str, str]]:
    if not content_el:
        return []

    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for span_i in content_el.select("span.i"):
        parsed = _parse_dotlist_item(span_i)
        if not parsed:
            continue
        key = parsed["name"] + parsed.get("note", "")
        if key in seen:
            continue
        seen.add(key)
        items.append(parsed)
    return items


def _parse_box_title(box: Tag) -> str:
    title_el = box.select_one("div.title > span")
    if not title_el:
        return ""
    return _rich_segments_to_text(_parse_tag_rich(title_el))


def parse_content_section(soup: BeautifulSoup) -> list[dict[str, Any]] | None:
    """解析 h2#内容 下的 infocard，按 main-heading / box 分组。"""
    h2 = find_section_h2(soup, "内容")
    if not h2:
        return None

    infocard = h2.find_next("div", class_="infocard")
    if not infocard:
        return None

    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for el in infocard.find_all("div"):
        classes = set(el.get("class") or [])
        if "main-heading" in classes:
            main = el.select_one(".main")
            heading = _clean_text(main.get_text()) if main else ""
            current = {"heading": heading, "boxes": []}
            groups.append(current)
            continue
        if "box" not in classes:
            continue
        if current is None:
            current = {"heading": "", "boxes": []}
            groups.append(current)
        title = _parse_box_title(el)
        content_el = el.select_one("div.content")
        items = _parse_box_items(content_el)
        if title or items:
            current["boxes"].append({"title": title, "items": items})

    groups = [g for g in groups if g.get("boxes")]
    return groups or None


def collect_content_image_filenames(content: list[dict[str, Any]] | None) -> list[str]:
    filenames: list[str] = []
    if not content:
        return filenames
    seen: set[str] = set()
    for group in content:
        for box in group.get("boxes") or []:
            for item in box.get("items") or []:
                fn = item.get("image")
                if fn and fn not in seen:
                    seen.add(fn)
                    filenames.append(fn)
    return filenames
