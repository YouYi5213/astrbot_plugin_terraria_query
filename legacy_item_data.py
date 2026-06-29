"""前代主机/3DS 旧版物品：Boss 掉落、navbox 目录与合成链扩展。"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

try:
    from .boss_data import LEGACY_BOSS_WIKI_TITLES, load_bosses_for_plugin
    from .legacy_metadata import (
        SOURCE_ARMOR_SET_PIECE,
        SOURCE_BOSS_DROP,
        SOURCE_CRAFT_CHAIN,
        SOURCE_NAVBOX,
        SOURCE_VARIANT_PAGE,
        apply_legacy_item_metadata,
        inherit_legacy_item_from_parent,
    )
    from .prepare_data import (
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _apply_overview_page_meta,
        _apply_search_aliases,
        _clean_text,
        _collect_image_urls,
        _filename_from_url,
        _image_url_from_src,
        _is_set_item,
        _merge_set_pieces,
        download_image,
        fetch_page_html,
        parse_item_page,
        parse_recipe_table,
    )
except ImportError:
    from boss_data import LEGACY_BOSS_WIKI_TITLES, load_bosses_for_plugin
    from legacy_metadata import (
        SOURCE_ARMOR_SET_PIECE,
        SOURCE_BOSS_DROP,
        SOURCE_CRAFT_CHAIN,
        SOURCE_NAVBOX,
        SOURCE_VARIANT_PAGE,
        apply_legacy_item_metadata,
        inherit_legacy_item_from_parent,
    )
    from prepare_data import (
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _apply_overview_page_meta,
        _apply_search_aliases,
        _clean_text,
        _collect_image_urls,
        _filename_from_url,
        _image_url_from_src,
        _is_set_item,
        _merge_set_pieces,
        download_image,
        fetch_page_html,
        parse_item_page,
        parse_recipe_table,
    )

LEGACY_NAVBOX_SOURCE_PAGE = "龙盔甲"
LEGACY_ITEM_MAX_EXPAND_PASSES = 10

# navbox 中跳过的一般性链接
_NAVBOX_SKIP_TITLES = frozenset(
    {
        "武器",
        "弹药",
        "盔甲",
        "时装物品",
        "前代主机版",
        "3DS版",
        "制作",
        "制作站",
        "困难模式",
        "Boss",
        "宠物",
        "坐骑",
        "敌怪",
        "其他",
    }
)

# 配方表链接中跳过（平台/版本页，非物品）
_RECIPE_EXPAND_SKIP = frozenset(
    {
        "PlayStation 3",
        "PlayStation Vita",
        "Wii U",
        "Xbox 360",
        "Xbox One",
        "Nintendo Switch",
        "电脑版",
        "主机版",
        "移动版",
        "前代主机版",
        "3DS版",
    }
)


def _page_html_path(wiki_title: str) -> str:
    return os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{wiki_title}.html")


def _load_page_html_local(wiki_title: str) -> str | None:
    path = _page_html_path(wiki_title)
    if not os.path.isfile(path):
        return None
    return open(path, "r", encoding="utf-8").read()


def load_old_gen_item_titles_from_navbox(
    source_page: str = LEGACY_NAVBOX_SOURCE_PAGE,
    *,
    path: str | None = None,
) -> list[str]:
    """从龙盔甲等页的前代主机/3DS navbox 收集物品 Wiki 标题。"""
    html_path = path or _page_html_path(source_page)
    if not os.path.isfile(html_path):
        return []
    soup = BeautifulSoup(open(html_path, "r", encoding="utf-8").read(), "lxml")
    titles: set[str] = set()
    for nav in soup.select("div.ranger-navbox"):
        heading = nav.select_one(".ranger-title-text")
        if not heading:
            continue
        text = heading.get_text()
        if "前代主机" not in text and "3DS" not in text:
            continue
        for a in nav.select("a[title]"):
            title = (a.get("title") or "").strip()
            if not title or title.startswith("Category:"):
                continue
            if title in _NAVBOX_SKIP_TITLES:
                continue
            titles.add(title)
    return sorted(titles)


def collect_legacy_boss_drop_titles(
    bosses: dict[str, dict] | None = None,
) -> list[str]:
    """从旧版 Boss 镜像页解析掉落物名（不依赖 bosses.json 中的条目）。"""
    from boss_data import LEGACY_BOSS_WIKI_TITLES, parse_boss_page_file

    names: set[str] = set()
    for wiki_title in LEGACY_BOSS_WIKI_TITLES:
        if bosses and wiki_title in bosses:
            boss = bosses[wiki_title]
        else:
            boss = parse_boss_page_file(wiki_title) or {}
        items_by_mode = (boss.get("drops") or {}).get("items") or {}
        for mode_items in items_by_mode.values():
            for entry in mode_items:
                if entry.get("type") == "item" and entry.get("name"):
                    names.add(entry["name"])
    return sorted(names)


def _item_indexed(items: dict[str, dict], name: str) -> bool:
    if not name:
        return True
    if name in items:
        return True
    for item in items.values():
        if item.get("wiki_title") == name or item.get("name") == name:
            return True
    return False


def _recipe_names_from_item(item: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def add_recipe(recipe: dict | None) -> None:
        if not recipe:
            return
        result = (recipe.get("result") or {}).get("name") or ""
        if result:
            names.add(result)
        for ing in recipe.get("ingredients") or []:
            iname = ing.get("name") or ""
            if iname:
                names.add(iname)

    add_recipe(item.get("recipe"))
    for piece in item.get("set_pieces") or []:
        add_recipe(piece.get("recipe"))
    return names


def _recipe_names_from_html(html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    names: set[str] = set()
    for table in soup.select("table.terraria.cellborder.recipes"):
        for recipe in parse_recipe_table(table):
            result = (recipe.get("result") or {}).get("name") or ""
            if result:
                names.add(result)
            for ing in recipe.get("ingredients") or []:
                iname = ing.get("name") or ""
                if iname:
                    names.add(iname)
    return names


def build_legacy_item_seed_titles(
    items: dict[str, dict] | None = None,
    bosses: dict[str, dict] | None = None,
) -> dict[str, str]:
    """返回 {wiki_title: legacy_source}。"""
    seeds: dict[str, str] = {}
    for title in load_old_gen_item_titles_from_navbox():
        seeds[title] = SOURCE_NAVBOX
    for title in collect_legacy_boss_drop_titles(bosses):
        seeds[title] = SOURCE_BOSS_DROP
    return seeds


def expand_legacy_item_titles(
    seeds: dict[str, str] | list[str],
    items: dict[str, dict],
    *,
    max_passes: int = LEGACY_ITEM_MAX_EXPAND_PASSES,
) -> dict[str, str]:
    """BFS：把已解析页内配方表中的材料/产物加入待抓取列表。"""
    if isinstance(seeds, list):
        seed_map = {t: SOURCE_CRAFT_CHAIN for t in seeds}
    else:
        seed_map = dict(seeds)
    seen: dict[str, str] = dict(seed_map)
    queue = [t for t in seed_map if not _item_indexed(items, t)]
    passes = 0

    while queue and passes < max_passes:
        passes += 1
        next_queue: list[str] = []
        for title in queue:
            html = _load_page_html_local(title)
            if not html:
                continue
            related = _recipe_names_from_html(html)
            item = items.get(title)
            if not item:
                for value in items.values():
                    if value.get("wiki_title") == title or value.get("name") == title:
                        item = value
                        break
            if item:
                related.update(_recipe_names_from_item(item))
            for name in related:
                if name in seen or _item_indexed(items, name):
                    continue
                if name in _RECIPE_EXPAND_SKIP:
                    continue
                if not os.path.isfile(_page_html_path(name)):
                    continue
                seen[name] = SOURCE_CRAFT_CHAIN
                next_queue.append(name)
        queue = next_queue

    return seen


def _apply_legacy_item_metadata(item: dict[str, Any], *, source: str) -> None:
    apply_legacy_item_metadata(item, source=source)


def _legacy_source_for_title(title: str, seed_sources: dict[str, str]) -> str:
    return seed_sources.get(title, SOURCE_CRAFT_CHAIN)


def _find_variant_wrapper(soup: BeautifulSoup, title: str):
    for anchor in soup.select("a"):
        if _clean_text(anchor.get_text()) == title:
            for parent_tag in ("span", "li", "td"):
                wrapper = anchor.find_parent(parent_tag)
                if wrapper and wrapper.select_one("img"):
                    return wrapper
    for element in soup.select("[title]"):
        if _clean_text(element.get("title") or "") == title:
            wrapper = element.find_parent("span") or element.find_parent("td")
            if wrapper and wrapper.select_one("img"):
                return wrapper
    for img in soup.select("img"):
        alt = img.get("alt") or img.get("title") or ""
        if alt == title or alt.startswith(f"{title}的"):
            wrapper = img.find_parent("span") or img.find_parent("td") or img.find_parent("li")
            if wrapper:
                return wrapper
    return None


def _variant_image_from_wrapper(wrapper) -> str:
    for img in wrapper.select("img"):
        alt = img.get("alt") or img.get("title") or ""
        if "放置" in alt:
            continue
        src = img.get("src")
        if src:
            return _filename_from_url(_image_url_from_src(src))
    return ""


def _parse_list_variant_item(html: str, title: str) -> dict | None:
    """从重定向到总览页的 Wiki 页面中提取具体变体（纪念章、八音盒等）。"""
    soup = BeautifulSoup(html, "lxml")
    wrapper = _find_variant_wrapper(soup, title)
    if not wrapper:
        return None
    image = _variant_image_from_wrapper(wrapper)
    if not image:
        return None
    return {
        "name": title,
        "image": image,
        "stats": [],
        "recipe": None,
    }


def _resolve_legacy_item(html: str, title: str) -> dict | None:
    base = parse_item_page(html, title)
    if base and base.get("name") == title:
        return base

    variant = _parse_list_variant_item(html, title)
    if variant:
        if base:
            if base.get("stats") and not variant.get("stats"):
                variant["stats"] = base["stats"]
            if base.get("description") and not variant.get("description"):
                variant["description"] = base["description"]
            if base.get("page_type") and not variant.get("page_type"):
                variant["page_type"] = base["page_type"]
        return variant

    if base and base.get("name") != title:
        cloned = dict(base)
        cloned["name"] = title
        return cloned
    return base


def _ingest_one_title(
    items: dict[str, dict],
    title: str,
    html: str,
    seed_sources: dict[str, str],
    *,
    force: bool = False,
) -> bool:
    item = _resolve_legacy_item(html, title)
    if not item:
        return False
    name = item["name"]
    if name != title and not _item_indexed(items, title):
        item = dict(item)
        item["name"] = title
        name = title
    if not force and _item_indexed(items, name):
        return False
    item["wiki_title"] = title
    legacy_source = _legacy_source_for_title(title, seed_sources)
    if _parse_list_variant_item(html, title) and legacy_source == SOURCE_CRAFT_CHAIN:
        legacy_source = SOURCE_VARIANT_PAGE
    _apply_legacy_item_metadata(item, source=legacy_source)
    _apply_overview_page_meta(item, title)
    _apply_search_aliases(item)
    items[name] = item
    if _is_set_item(item):
        _merge_set_pieces(items, name, item)
        for piece in item.get("set_pieces") or []:
            pname = piece.get("name")
            if pname and pname in items:
                inherit_legacy_item_from_parent(items[pname], item, parent_set=name)
    return True


async def ingest_legacy_items_into(
    items: dict[str, dict],
    session: aiohttp.ClientSession | None = None,
    *,
    force: bool = False,
    download_images: bool = True,
) -> dict[str, Any]:
    """抓取旧版物品（Boss 掉落 + navbox + 合成链）并写入 items 字典。"""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    bosses = load_bosses_for_plugin()
    seed_sources = build_legacy_item_seed_titles(items, bosses)
    title_sources = expand_legacy_item_titles(seed_sources, items)

    new_count = 0
    image_urls: dict[str, str] = {}

    for _round in range(3):
        if force:
            pending = list(title_sources.keys())
        else:
            pending = [t for t in title_sources if not _item_indexed(items, t)]
        if not pending:
            break
        round_added = 0
        for title in pending:
            html: str | None = None
            if session is not None:
                html = await fetch_page_html(session, title)
                await asyncio.sleep(0.08)
            if not html:
                html = _load_page_html_local(title)
            if not html:
                continue
            if not _ingest_one_title(items, title, html, title_sources, force=force):
                continue
            round_added += 1
            for value in items.values():
                if value.get("wiki_title") == title or value.get("name") == title:
                    image_urls.update(_collect_image_urls(value))
                    for piece in value.get("set_pieces") or []:
                        image_urls.update(_collect_image_urls(piece))
                    break
        new_count += round_added
        if round_added == 0:
            break
        title_sources = expand_legacy_item_titles(seed_sources, items)

    images_ok = 0
    images_total = 0
    if download_images and image_urls and session is not None:
        pending = {
            fn: url
            for fn, url in image_urls.items()
            if fn and not os.path.isfile(os.path.join(IMAGES_DIR, fn))
        }
        images_total = len(pending)
        if pending:
            semaphore = asyncio.Semaphore(8)
            results = await asyncio.gather(
                *[
                    download_image(session, fn, url, semaphore)
                    for fn, url in pending.items()
                ]
            )
            images_ok = sum(1 for r in results if r)

    return {
        "seed_count": len(seed_sources),
        "title_count": len(title_sources),
        "new_count": new_count,
        "images_ok": images_ok,
        "images_total": images_total,
    }


def ingest_legacy_items_local(
    items: dict[str, dict] | None = None,
    *,
    force: bool = False,
    download_images: bool = True,
) -> dict[str, Any]:
    items = items if items is not None else {}
    if download_images:

        async def _run() -> dict[str, Any]:
            connector = aiohttp.TCPConnector(limit=10)
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout
            ) as session:
                return await ingest_legacy_items_into(
                    items,
                    session,
                    force=force,
                    download_images=True,
                )

        return asyncio.run(_run())
    return asyncio.run(
        ingest_legacy_items_into(items, None, force=force, download_images=False)
    )
