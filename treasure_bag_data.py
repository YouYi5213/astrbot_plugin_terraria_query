"""泰拉瑞亚宝藏袋：总览页解析与 treasure_bags.json 持久化。"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup, Tag

try:
    from .prepare_data import (
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        _parse_tag_rich,
        _rich_segments_to_text,
        download_image,
    )
except ImportError:
    from prepare_data import (
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        _parse_tag_rich,
        _rich_segments_to_text,
        download_image,
    )

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_PLUGIN_DIR, "data", "terraria_query")
CATEGORIES_DIR = os.path.join(DATA_DIR, "categories")
TREASURE_BAGS_JSON = os.path.join(CATEGORIES_DIR, "treasure_bags.json")

OVERVIEW_WIKI_TITLE = "宝藏袋"
_EXPERT_ROW_STYLE = "127,127,127"
_ITEM_ID_RE = re.compile(r"(\d+)")

TREASURE_BAG_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "独眼巨鹿": ("鹿角怪",),
    "猪龙鱼公爵": ("猪鲨",),
    "月亮领主": ("月总",),
}


def _overview_html_path() -> str:
    return os.path.join(
        _WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{OVERVIEW_WIKI_TITLE}.html"
    )


def _is_expert_row(tr: Tag) -> bool:
    style = tr.get("style") or ""
    return _EXPERT_ROW_STYLE in style


def _parse_item_cell(td: Tag) -> dict[str, Any]:
    link = td.select_one("a[title]")
    name = _clean_text(link.get("title", "")) if link else _clean_text(td.get_text())
    image = ""
    img = td.select_one("img")
    if img and img.get("src"):
        image = _filename_from_url(_image_url_from_src(img["src"]))
    note_parts: list[str] = []
    for note_span in td.select("span.note2, span.note"):
        note = _clean_text(note_span.get_text())
        if note:
            note_parts.append(note)
    label = name + "".join(note_parts) if note_parts else name
    entry: dict[str, Any] = {"name": name, "label": label}
    if image:
        entry["image"] = image
    return entry


def _parse_quantity_cell(td: Tag) -> dict[str, Any]:
    segments = _parse_tag_rich(td)
    text = _rich_segments_to_text(segments)
    coins = [
        {
            "type": seg.get("coin_type", ""),
            "amount": seg.get("amount", ""),
        }
        for seg in segments
        if seg.get("type") == "coin" and seg.get("coin_type")
    ]
    result: dict[str, Any] = {"quantity": text}
    if coins:
        result["quantity_coins"] = coins
    return result


def _parse_table_header(table: Tag) -> tuple[str, str, str]:
    th = table.find("th")
    if not th:
        return "", "", ""

    bag_img = th.select_one("img")
    image = ""
    if bag_img and bag_img.get("src"):
        image = _filename_from_url(_image_url_from_src(bag_img["src"]))

    boss_name = ""
    for link in th.select("a[title]"):
        title = _clean_text(link.get("title", ""))
        if not title or title.startswith("宝藏袋"):
            continue
        boss_name = title
        break

    item_id = ""
    id_span = th.select_one("span.id")
    if id_span:
        match = _ITEM_ID_RE.search(_clean_text(id_span.get_text()))
        if match:
            item_id = match.group(1)

    anchor_id = table.get("id") or ""
    return boss_name, image, item_id if item_id else anchor_id


def parse_treasure_bag_table(table: Tag) -> dict[str, Any] | None:
    boss_name, bag_image, item_id = _parse_table_header(table)
    if not boss_name:
        return None

    drops: list[dict[str, Any]] = []
    for tr in table.select("tr"):
        if tr.find("th"):
            continue
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        item = _parse_item_cell(tds[0])
        if not item.get("name"):
            continue
        chance = _clean_text(tds[1].get_text())
        qty = _parse_quantity_cell(tds[2])
        row: dict[str, Any] = {
            **item,
            "chance": chance,
            **qty,
        }
        if _is_expert_row(tr):
            row["expert_exclusive"] = True
        drops.append(row)

    if not drops:
        return None

    search_terms = list(TREASURE_BAG_SEARCH_ALIASES.get(boss_name, ()))
    for alias in list(search_terms):
        search_terms.append(f"{alias}宝藏袋")
    search_terms.append(f"{boss_name}宝藏袋")
    search_terms.append(f"宝藏袋（{boss_name}）")

    return {
        "name": boss_name,
        "wiki_title": boss_name,
        "page_type": "treasure_bag",
        "anchor_id": table.get("id") or "",
        "image": bag_image,
        "item_id": item_id if item_id.isdigit() else "",
        "drops": drops,
        "search_terms": sorted(set(search_terms)),
    }


def parse_treasure_bags_from_soup(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """解析 h2#类型 至下一 h2 之间的宝藏袋表格。"""
    h2 = None
    for candidate in soup.find_all("h2"):
        headline = candidate.find(class_="mw-headline")
        if headline and headline.get("id", "").startswith("类型"):
            h2 = candidate
            break
    if not h2:
        return []

    bags: list[dict[str, Any]] = []
    for sib in h2.find_next_siblings():
        if not isinstance(sib, Tag):
            continue
        if sib.name == "h2":
            break
        for table in sib.select("table.terraria[id]"):
            parsed = parse_treasure_bag_table(table)
            if parsed:
                bags.append(parsed)
    return bags


def parse_treasure_bags_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    return parse_treasure_bags_from_soup(soup)


def parse_treasure_bags_file(path: str | None = None) -> list[dict[str, Any]]:
    page_path = path or _overview_html_path()
    if not os.path.isfile(page_path):
        return []
    html = open(page_path, "r", encoding="utf-8").read()
    return parse_treasure_bags_html(html)


def build_treasure_bags_from_mirror() -> dict[str, dict]:
    bags: dict[str, dict] = {}
    for entry in parse_treasure_bags_file():
        key = entry.get("name") or entry.get("anchor_id")
        if key:
            bags[key] = entry
    return bags


def load_treasure_bags_for_plugin(categories_dir: str = CATEGORIES_DIR) -> dict[str, dict]:
    path = os.path.join(categories_dir, "treasure_bags.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _collect_treasure_bag_image_urls(bags: dict[str, dict]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for bag in bags.values():
        fn = bag.get("image")
        if fn:
            urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
        for drop in bag.get("drops") or []:
            drop_fn = drop.get("image")
            if drop_fn:
                urls[drop_fn] = f"https://terraria.wiki.gg/images/{quote(drop_fn, safe='')}"
    return urls


def _patch_manifest_treasure_bag_count(count: int, categories_dir: str = CATEGORIES_DIR) -> None:
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

    entry = {
        "key": "treasure_bags",
        "label": "宝藏袋",
        "file": "treasure_bags.json",
        "count": count,
    }
    old_count = 0
    updated = False
    for i, cat in enumerate(categories):
        if cat.get("key") == "treasure_bags":
            old_count = int(cat.get("count") or 0)
            categories[i] = entry
            updated = True
            break
    if not updated:
        categories.append(entry)

    manifest["categories"] = categories
    old_total = manifest.get("total", 0)
    if isinstance(old_total, int):
        manifest["total"] = old_total - old_count + count

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


async def refresh_treasure_bags(
    session: aiohttp.ClientSession,
    *,
    force: bool = False,
) -> dict[str, Any]:
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

    parsed_list = parse_treasure_bags_file()
    if not parsed_list:
        return {
            "new_count": 0,
            "total": len(load_treasure_bags_for_plugin()),
            "error": "no_overview_page",
        }

    bags: dict[str, dict] = {} if force else load_treasure_bags_for_plugin()
    before = len(bags)
    for entry in parsed_list:
        key = entry.get("name") or entry.get("anchor_id")
        if not key:
            continue
        if not force and key in bags:
            continue
        bags[key] = entry

    image_urls = _collect_treasure_bag_image_urls(bags)
    images_ok = 0
    if image_urls:
        semaphore = asyncio.Semaphore(8)
        results = await asyncio.gather(
            *[
                download_image(session, fn, url, semaphore)
                for fn, url in image_urls.items()
            ]
        )
        images_ok = sum(1 for r in results if r)

    with open(TREASURE_BAGS_JSON, "w", encoding="utf-8") as f:
        json.dump(bags, f, ensure_ascii=False, indent=2)

    _patch_manifest_treasure_bag_count(len(bags))

    return {
        "new_count": max(0, len(bags) - before) if force else max(0, len(bags) - before),
        "total": len(bags),
        "images_ok": images_ok,
        "images_total": len(image_urls),
        "catalog_size": len(parsed_list),
    }


def ingest_treasure_bags_local(*, force: bool = False, download_images: bool = True) -> dict[str, Any]:
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    bags = build_treasure_bags_from_mirror()
    if not force:
        existing = load_treasure_bags_for_plugin()
        for key, value in existing.items():
            bags.setdefault(key, value)

    with open(TREASURE_BAGS_JSON, "w", encoding="utf-8") as f:
        json.dump(bags, f, ensure_ascii=False, indent=2)

    _patch_manifest_treasure_bag_count(len(bags))
    result: dict[str, Any] = {
        "total": len(bags),
        "catalog_size": len(parse_treasure_bags_file()),
    }

    if download_images and bags:
        async def _run() -> dict[str, int]:
            connector = aiohttp.TCPConnector(limit=10)
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout
            ) as session:
                return await refresh_treasure_bags(session, force=True)

        img_result = asyncio.run(_run())
        result.update(
            {
                "images_ok": img_result.get("images_ok", 0),
                "images_total": img_result.get("images_total", 0),
            }
        )
    return result
