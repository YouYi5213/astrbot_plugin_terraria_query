"""泰拉瑞亚生物群系：主页目录解析、页面抓取与 biomes.json 持久化。"""

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
    from .page_section_data import (
        collect_content_image_filenames,
        collect_page_content_image_urls,
        parse_conditions_section,
        parse_content_section,
    )
    from .prepare_data import (
        HEADERS,
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        download_image,
        parse_description_from_soup,
    )
except ImportError:
    from page_section_data import (
        collect_content_image_filenames,
        collect_page_content_image_urls,
        parse_conditions_section,
        parse_content_section,
    )
    from prepare_data import (
        HEADERS,
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        download_image,
        parse_description_from_soup,
    )

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_PLUGIN_DIR, "data", "terraria_query")
CATEGORIES_DIR = os.path.join(DATA_DIR, "categories")
BIOMES_JSON = os.path.join(CATEGORIES_DIR, "biomes.json")
MANIFEST_JSON = os.path.join(CATEGORIES_DIR, "manifest.json")

HOMEPAGE_TITLE = "Terraria Wiki"
BIOME_BOX_ID = "box-biomes"

# 玩家常用俗称 / 主页别名 → 便于搜索
BIOME_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "地下": ("地下层",),
    "洞穴": ("洞穴层",),
    "腐化之地": ("腐化",),
    "猩红之地": ("猩红",),
    "神圣之地": ("神圣",),
    "雪原生物群系": ("雪原",),
    "冰雪生物群系": ("冰雪",),
    "发光蘑菇生物群系": ("发光蘑菇", "蘑菇生物群系"),
}


def _homepage_html_path() -> str:
    return os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{HOMEPAGE_TITLE}.html")


def _biome_page_html_path(wiki_title: str) -> str:
    return os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{wiki_title}.html")


def load_biome_catalog_from_homepage(path: str | None = None) -> list[dict[str, str]]:
    """从 Wiki 主页 #box-biomes 读取群系列表。"""
    html_path = path or _homepage_html_path()
    if not os.path.isfile(html_path):
        return []
    html = open(html_path, "r", encoding="utf-8").read()
    soup = BeautifulSoup(html, "html.parser")
    box = soup.find(id=BIOME_BOX_ID)
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
            img = li.select_one("img")
            image = ""
            if img and img.get("src"):
                image = _filename_from_url(_image_url_from_src(img["src"]))
            entries.append(
                {
                    "wiki_title": wiki_title,
                    "label": label,
                    "image": image,
                }
            )
            seen.add(wiki_title)
    return entries


def _parse_biome_banner(soup: BeautifulSoup) -> str:
    img = soup.select_one(
        "div.center img[alt^=BiomeBanner], div.center img[src*=BiomeBanner]"
    )
    if not img:
        img = soup.select_one("img[alt^=BiomeBanner]")
    if not img or not img.get("src"):
        return ""
    return _filename_from_url(_image_url_from_src(img["src"]))


def parse_biome_page_html(
    html: str,
    *,
    wiki_title: str,
    label: str | None = None,
) -> dict | None:
    """解析群系 Wiki 页：横幅图 + 导语描述。"""
    soup = BeautifulSoup(html, "html.parser")
    banner = _parse_biome_banner(soup)
    parsed_desc = parse_description_from_soup(soup)
    if not banner and not parsed_desc:
        return None

    display_name = label or wiki_title
    search_terms = list(BIOME_SEARCH_ALIASES.get(wiki_title, ()))
    if label and label != wiki_title:
        search_terms.append(label)

    item: dict[str, Any] = {
        "name": display_name,
        "wiki_title": wiki_title,
        "page_type": "biome",
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


def parse_biome_page_file(
    wiki_title: str,
    *,
    label: str | None = None,
    path: str | None = None,
) -> dict | None:
    page_path = path or _biome_page_html_path(wiki_title)
    if not os.path.isfile(page_path):
        return None
    html = open(page_path, "r", encoding="utf-8").read()
    return parse_biome_page_html(html, wiki_title=wiki_title, label=label)


def apply_biome_overview_metadata(
    biomes: dict[str, dict],
    catalog: list[dict[str, str]] | None = None,
) -> None:
    """写入总览用 list_icon（Wiki 主页小图标）。"""
    catalog = catalog if catalog is not None else load_biome_catalog_from_homepage()
    if not catalog:
        return
    by_title = {entry["wiki_title"]: entry for entry in catalog}
    by_label = {entry.get("label") or entry["wiki_title"]: entry for entry in catalog}
    for key, item in biomes.items():
        entry = (
            by_title.get(item.get("wiki_title", ""))
            or by_title.get(key)
            or by_label.get(key)
        )
        if not entry:
            continue
        if entry.get("image"):
            item["list_icon"] = entry["image"]


def build_biomes_from_mirror(
    catalog: list[dict[str, str]] | None = None,
) -> dict[str, dict]:
    catalog = catalog if catalog is not None else load_biome_catalog_from_homepage()
    biomes: dict[str, dict] = {}
    for entry in catalog:
        wiki_title = entry["wiki_title"]
        label = entry.get("label") or wiki_title
        parsed = parse_biome_page_file(wiki_title, label=label)
        if not parsed:
            continue
        key = parsed.get("name") or label
        biomes[key] = parsed
    return biomes


def load_biomes_for_plugin(categories_dir: str = CATEGORIES_DIR) -> dict[str, dict]:
    path = os.path.join(categories_dir, "biomes.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _collect_biome_image_urls(biomes: dict[str, dict]) -> dict[str, str]:
    urls = collect_page_content_image_urls(biomes)
    for item in biomes.values():
        fn = item.get("list_icon")
        if fn and fn not in urls:
            urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    return urls


def _patch_manifest_biome_count(count: int, categories_dir: str = CATEGORIES_DIR) -> None:
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

    biome_entry = {
        "key": "biomes",
        "label": "生物群系",
        "file": "biomes.json",
        "count": count,
    }
    old_biome_count = 0
    updated = False
    for i, cat in enumerate(categories):
        if cat.get("key") == "biomes":
            old_biome_count = int(cat.get("count") or 0)
            categories[i] = biome_entry
            updated = True
            break
    if not updated:
        categories.append(biome_entry)

    manifest["categories"] = categories
    old_total = manifest.get("total", 0)
    if isinstance(old_total, int):
        manifest["total"] = old_total - old_biome_count + count

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _load_existing_biomes() -> dict[str, dict]:
    return load_biomes_for_plugin(CATEGORIES_DIR)


async def refresh_biomes(
    session: aiohttp.ClientSession,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """从本地 Wiki 镜像抓取群系并写入 categories/biomes.json。"""
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

    catalog = load_biome_catalog_from_homepage()
    if not catalog:
        return {"new_count": 0, "total": len(_load_existing_biomes()), "error": "no_catalog"}

    biomes: dict[str, dict] = {} if force else _load_existing_biomes()
    before = len(biomes)
    new_count = 0

    for entry in catalog:
        wiki_title = entry["wiki_title"]
        label = entry.get("label") or wiki_title
        key = label
        if not force and key in biomes:
            continue
        parsed = parse_biome_page_file(wiki_title, label=label)
        if not parsed:
            continue
        biomes[key] = parsed
        new_count += 1

    apply_biome_overview_metadata(biomes, catalog)

    image_urls = _collect_biome_image_urls(biomes)
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

    with open(BIOMES_JSON, "w", encoding="utf-8") as f:
        json.dump(biomes, f, ensure_ascii=False, indent=2)

    _patch_manifest_biome_count(len(biomes))

    return {
        "new_count": new_count if force else max(0, len(biomes) - before),
        "total": len(biomes),
        "images_ok": images_ok,
        "images_total": images_total,
        "catalog_size": len(catalog),
    }


def ingest_biomes_local(*, force: bool = False, download_images: bool = True) -> dict[str, Any]:
    """仅使用本地镜像构建 biomes.json；默认补全内容区缺失图片。"""
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    biomes = build_biomes_from_mirror()
    if not force:
        existing = _load_existing_biomes()
        for key, value in existing.items():
            biomes.setdefault(key, value)

    apply_biome_overview_metadata(biomes)

    with open(BIOMES_JSON, "w", encoding="utf-8") as f:
        json.dump(biomes, f, ensure_ascii=False, indent=2)

    _patch_manifest_biome_count(len(biomes))
    result: dict[str, Any] = {
        "total": len(biomes),
        "catalog_size": len(load_biome_catalog_from_homepage()),
    }
    if download_images:
        try:
            from .page_section_data import backfill_page_content_images
        except ImportError:
            from page_section_data import backfill_page_content_images

        async def _run() -> dict[str, int]:
            connector = aiohttp.TCPConnector(limit=10)
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout
            ) as session:
                return await backfill_page_content_images(session, biomes=biomes)

        img_result = asyncio.run(_run())
        result.update(img_result)
    return result
