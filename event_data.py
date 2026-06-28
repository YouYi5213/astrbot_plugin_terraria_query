"""泰拉瑞亚事件：主页目录解析、页面抓取与 events.json 持久化。"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup, Tag

try:
    from .page_section_data import (
        collect_content_image_filenames,
        parse_conditions_section,
        parse_content_section,
    )
    from .prepare_data import (
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _append_description_list,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        _is_skippable_intro_table,
        _parse_description_list_block,
        _parse_description_paragraph_rich,
        _parse_tag_rich,
        _rich_segments_to_text,
        _should_skip_intro_div,
        download_image,
    )
except ImportError:
    from page_section_data import (
        collect_content_image_filenames,
        parse_conditions_section,
        parse_content_section,
    )
    from prepare_data import (
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _append_description_list,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        _is_skippable_intro_table,
        _parse_description_list_block,
        _parse_description_paragraph_rich,
        _parse_tag_rich,
        _rich_segments_to_text,
        _should_skip_intro_div,
        download_image,
    )

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_PLUGIN_DIR, "data", "terraria_query")
CATEGORIES_DIR = os.path.join(DATA_DIR, "categories")
EVENTS_JSON = os.path.join(CATEGORIES_DIR, "events.json")
MANIFEST_JSON = os.path.join(CATEGORIES_DIR, "manifest.json")

HOMEPAGE_TITLE = "Terraria Wiki"
EVENT_BOX_ID = "box-events"

EVENT_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "月亮事件": ("月柱", "四柱", "天界柱"),
    "日食": ("日蚀",),
    "撒旦军队": ("旧日军团",),
}


def _homepage_html_path() -> str:
    return os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{HOMEPAGE_TITLE}.html")


def _event_page_html_path(wiki_title: str) -> str:
    return os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{wiki_title}.html")


def load_event_catalog_from_homepage(path: str | None = None) -> list[dict[str, str]]:
    """从 Wiki 主页 #box-events 读取事件列表。"""
    html_path = path or _homepage_html_path()
    if not os.path.isfile(html_path):
        return []
    html = open(html_path, "r", encoding="utf-8").read()
    soup = BeautifulSoup(html, "html.parser")
    box = soup.find(id=EVENT_BOX_ID)
    if not box:
        return []

    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for li in box.select("li"):
        a = li.select_one("span.i a[title]") or li.select_one("a[title]")
        if not a:
            continue
        wiki_title = _clean_text(a.get("title", ""))
        label = _clean_text(a.get_text()) or wiki_title
        if wiki_title and wiki_title not in seen:
            entries.append({"wiki_title": wiki_title, "label": label})
            seen.add(wiki_title)
    return entries


def _parse_event_banner(soup: BeautifulSoup) -> str:
    img = soup.select_one(
        "div.center img[alt^=BiomeBanner], div.center img[src*=BiomeBanner]"
    )
    if not img:
        img = soup.select_one("img[alt^=BiomeBanner]")
    if not img or not img.get("src"):
        return ""
    return _filename_from_url(_image_url_from_src(img["src"]))


def _append_rich_paragraph(
    paragraphs: list[str],
    rich_paragraphs: list[list[dict]],
    rich: list[dict],
) -> None:
    text = _rich_segments_to_text(rich)
    if text:
        paragraphs.append(text)
        rich_paragraphs.append(rich)


def parse_event_description_from_soup(soup: BeautifulSoup) -> dict | None:
    """提取事件页导语：含引言 blockquote、flavor 行与开头段落（至目录前）。"""
    root = (
        soup.select_one("#mw-content-text .mw-parser-output")
        or soup.select_one(".mw-parser-output")
    )
    if not root:
        return None

    stop_tags = frozenset({"h2", "h3", "h4", "figure", "style", "script"})
    paragraphs: list[str] = []
    rich_paragraphs: list[list[dict]] = []

    for child in root.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "table":
            if _is_skippable_intro_table(child):
                continue
            break
        if child.name == "div":
            classes = set(child.get("class") or [])
            if child.select_one("img[alt^=BiomeBanner], img[src*=BiomeBanner]"):
                continue
            if "flavor-text" in classes:
                _append_rich_paragraph(
                    paragraphs, rich_paragraphs, _parse_tag_rich(child)
                )
                continue
            if child.get("id") == "toc" or classes & {"toc"}:
                break
            action = _should_skip_intro_div(child)
            if action == "skip":
                continue
            break
        if child.name == "blockquote":
            for para in child.select("p"):
                _append_rich_paragraph(
                    paragraphs,
                    rich_paragraphs,
                    _parse_description_paragraph_rich(para),
                )
            continue
        if child.name == "p":
            rich = _parse_description_paragraph_rich(child)
            _append_rich_paragraph(paragraphs, rich_paragraphs, rich)
            continue
        if child.name in ("ul", "ol"):
            parsed_list = _parse_description_list_block(child)
            if parsed_list:
                text, rich = parsed_list
                _append_description_list(paragraphs, rich_paragraphs, text, rich)
            continue
        if child.name in stop_tags:
            break
        break

    if not paragraphs:
        return None
    return {
        "text": "\n\n".join(paragraphs),
        "rich": rich_paragraphs,
    }


def parse_event_page_html(
    html: str,
    *,
    wiki_title: str,
    label: str | None = None,
) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    banner = _parse_event_banner(soup)
    parsed_desc = parse_event_description_from_soup(soup)
    if not banner and not parsed_desc:
        return None

    display_name = label or wiki_title
    search_terms = list(EVENT_SEARCH_ALIASES.get(wiki_title, ()))
    if label and label != wiki_title:
        search_terms.append(label)

    item: dict[str, Any] = {
        "name": display_name,
        "wiki_title": wiki_title,
        "page_type": "event",
        "image": banner,
        "search_terms": sorted(set(search_terms)),
    }
    if parsed_desc:
        item["description"] = parsed_desc["text"]
        item["description_rich"] = parsed_desc["rich"]

    conditions = parse_conditions_section(soup)
    if conditions:
        item["conditions"] = conditions["text"]
        item["conditions_rich"] = conditions["rich"]

    content = parse_content_section(soup)
    if content:
        item["content"] = content
    return item


def parse_event_page_file(
    wiki_title: str,
    *,
    label: str | None = None,
    path: str | None = None,
) -> dict | None:
    page_path = path or _event_page_html_path(wiki_title)
    if not os.path.isfile(page_path):
        return None
    html = open(page_path, "r", encoding="utf-8").read()
    return parse_event_page_html(html, wiki_title=wiki_title, label=label)


def build_events_from_mirror(
    catalog: list[dict[str, str]] | None = None,
) -> dict[str, dict]:
    catalog = catalog if catalog is not None else load_event_catalog_from_homepage()
    events: dict[str, dict] = {}
    for entry in catalog:
        wiki_title = entry["wiki_title"]
        label = entry.get("label") or wiki_title
        parsed = parse_event_page_file(wiki_title, label=label)
        if not parsed:
            continue
        key = parsed.get("name") or label
        events[key] = parsed
    return events


def load_events_for_plugin(categories_dir: str = CATEGORIES_DIR) -> dict[str, dict]:
    path = os.path.join(categories_dir, EVENTS_JSON)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _collect_event_image_urls(events: dict[str, dict]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for event in events.values():
        fn = event.get("image")
        if fn:
            urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
        for content_fn in collect_content_image_filenames(event.get("content")):
            urls[content_fn] = f"https://terraria.wiki.gg/images/{quote(content_fn, safe='')}"
    return urls


def _patch_manifest_event_count(count: int, categories_dir: str = CATEGORIES_DIR) -> None:
    manifest_path = os.path.join(categories_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        return
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    categories = manifest.get("categories")
    if not isinstance(categories, list):
        categories = []

    event_entry = {
        "key": "events",
        "label": "事件",
        "file": "events.json",
        "count": count,
    }
    old_event_count = 0
    updated = False
    for i, cat in enumerate(categories):
        if cat.get("key") == "events":
            old_event_count = int(cat.get("count") or 0)
            categories[i] = event_entry
            updated = True
            break
    if not updated:
        categories.append(event_entry)

    manifest["categories"] = categories
    old_total = manifest.get("total", 0)
    if isinstance(old_total, int):
        manifest["total"] = old_total - old_event_count + count

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _load_existing_events() -> dict[str, dict]:
    return load_events_for_plugin(CATEGORIES_DIR)


async def refresh_events(
    session: aiohttp.ClientSession,
    *,
    force: bool = False,
) -> dict[str, Any]:
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

    catalog = load_event_catalog_from_homepage()
    if not catalog:
        return {"new_count": 0, "total": len(_load_existing_events()), "error": "no_catalog"}

    events: dict[str, dict] = {} if force else _load_existing_events()
    before = len(events)
    new_count = 0

    for entry in catalog:
        wiki_title = entry["wiki_title"]
        label = entry.get("label") or wiki_title
        key = label
        if not force and key in events:
            continue
        parsed = parse_event_page_file(wiki_title, label=label)
        if not parsed:
            continue
        events[key] = parsed
        new_count += 1

    image_urls = _collect_event_image_urls(events)
    images_total = len(image_urls)
    images_ok = 0
    if image_urls:
        semaphore = asyncio.Semaphore(8)
        tasks = [
            download_image(session, fn, url, semaphore)
            for fn, url in image_urls.items()
        ]
        results = await asyncio.gather(*tasks)
        images_ok = sum(1 for r in results if r)

    with open(EVENTS_JSON, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

    _patch_manifest_event_count(len(events))

    return {
        "new_count": new_count if force else max(0, len(events) - before),
        "total": len(events),
        "images_ok": images_ok,
        "images_total": images_total,
        "catalog_size": len(catalog),
    }


def ingest_events_local(*, force: bool = False) -> dict[str, Any]:
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    events = build_events_from_mirror()
    if not force:
        existing = _load_existing_events()
        for key, value in existing.items():
            events.setdefault(key, value)

    with open(EVENTS_JSON, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

    _patch_manifest_event_count(len(events))
    return {"total": len(events), "catalog_size": len(load_event_catalog_from_homepage())}
