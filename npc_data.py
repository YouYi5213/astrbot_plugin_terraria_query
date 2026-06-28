"""泰拉瑞亚城镇 NPC：概览目录解析、页面抓取与 npcs.json 持久化。"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import quote, unquote

import aiohttp
from bs4 import BeautifulSoup, Tag

try:
    from .prepare_data import (
        COIN_SPECS,
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        download_image,
    )
except ImportError:
    from prepare_data import (
        COIN_SPECS,
        IMAGES_DIR,
        _WIKI_MIRROR_DIR,
        _clean_text,
        _filename_from_url,
        _image_url_from_src,
        download_image,
    )

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_PLUGIN_DIR, "data", "terraria_query")
CATEGORIES_DIR = os.path.join(DATA_DIR, "categories")
NPCS_JSON = os.path.join(CATEGORIES_DIR, "npcs.json")
MANIFEST_JSON = os.path.join(CATEGORIES_DIR, "manifest.json")

NPC_OVERVIEW_PAGE = "NPC"
_TOWN_TABLE_HEADS = frozenset({"绘像", "NPC"})
_SPAWN_INTRO_KEYS = ("出现", "生成", "条件", "满足", "存在")


def _overview_html_path() -> str:
    return os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{NPC_OVERVIEW_PAGE}.html")


def _npc_page_html_path(wiki_title: str) -> str:
    return os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{wiki_title}.html")


def _strip_refs(el: Tag) -> None:
    for sup in el.select("sup.reference"):
        sup.decompose()


def _cell_text(el: Tag | None, *, multiline: bool = False) -> str:
    if not el:
        return ""
    clone = BeautifulSoup(str(el), "lxml")
    node = clone.select_one("td") or clone.select_one("p") or clone.select_one("li") or clone
    _strip_refs(node)
    sep = "\n" if multiline else " "
    text = node.get_text(sep, strip=True)
    if multiline:
        text = re.sub(r"\n{2,}", "\n", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return _clean_text(text)


def _portrait_from_link(a: Tag | None) -> str:
    if not a:
        return ""
    href = a.get("href", "")
    if "/File:" in href or "File:" in href:
        name = unquote(href.split("File:")[-1].split("?")[0])
        return _filename_from_url(name)
    img = a.select_one("img")
    if img and img.get("src"):
        return _filename_from_url(_image_url_from_src(img["src"]))
    return ""


def _image_from_tag(img: Tag | None) -> str:
    if img and img.get("src"):
        return _filename_from_url(_image_url_from_src(img["src"]))
    return ""


def _parse_sprite_from_td(td: Tag) -> str:
    img = td.select_one("span.i img") or td.select_one("img")
    return _image_from_tag(img)


def _parse_overview_row(tr: Tag) -> dict[str, str] | None:
    tds = tr.find_all("td", recursive=False)
    if len(tds) < 5:
        return None
    name_a = tds[2].select_one("a[title]")
    if not name_a:
        return None
    wiki_title = _clean_text(name_a.get("title", ""))
    if not wiki_title:
        return None
    sprite = _parse_sprite_from_td(tds[1])
    portrait = _portrait_from_link(tds[0].select_one("a.image") or tds[0].select_one("a"))
    return {
        "wiki_title": wiki_title,
        "label": wiki_title,
        "sprite": sprite,
        "portrait": portrait,
        "description": _cell_text(tds[3]),
        "spawn_overview": _cell_text(tds[4]),
    }


def _parse_town_npc_table(table: Tag, *, phase: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tr in table.select("tbody tr"):
        th = tr.find("th")
        if th:
            continue
        parsed = _parse_overview_row(tr)
        if parsed:
            parsed["phase"] = phase
            rows.append(parsed)
    return rows


def _section_h2(soup: BeautifulSoup, section_id: str) -> Tag | None:
    anchor = soup.find(id=section_id)
    if not anchor:
        return None
    if anchor.name == "h2":
        return anchor
    return anchor.find_parent("h2")


def load_npc_catalog_from_overview(path: str | None = None) -> list[dict[str, str]]:
    """从 NPC.html 城镇 NPC 与其他 NPC 表格读取目录。"""
    html_path = path or _overview_html_path()
    if not os.path.isfile(html_path):
        return []

    soup = BeautifulSoup(open(html_path, "r", encoding="utf-8").read(), "html.parser")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()

    town_section = _section_h2(soup, "城镇_NPC")
    if town_section:
        node = town_section
        while True:
            node = node.find_next_sibling()
            if not node:
                break
            if isinstance(node, Tag) and node.name == "h2":
                break
            if isinstance(node, Tag) and node.name == "h3":
                headline = node.find(class_="mw-headline")
                section_id = headline.get("id", "") if headline else ""
                table = node.find_next_sibling("table")
                if not table:
                    continue
                phase = "hardmode" if section_id == "困难模式" else "pre_hardmode"
                for row in _parse_town_npc_table(table, phase=phase):
                    if row["wiki_title"] not in seen:
                        seen.add(row["wiki_title"])
                        entries.append(row)

    other_section = _section_h2(soup, "其他_NPC")
    if other_section:
        table = other_section.find_next("table")
        if table:
            for row in _parse_town_npc_table(table, phase="other"):
                if row["wiki_title"] not in seen:
                    seen.add(row["wiki_title"])
                    entries.append(row)

    return entries


def _find_section_h2(soup: BeautifulSoup, section_prefix: str) -> Tag | None:
    for h2 in soup.find_all("h2"):
        headline = h2.find(class_="mw-headline")
        if headline and headline.get("id", "").startswith(section_prefix):
            return h2
    return None


def _parse_npc_sprite(infobox: Tag) -> str:
    """NPC 立绘（infobox 内站立 sprite，非对话肖像）。"""
    img = infobox.select_one('div.section.images span[title*="电脑"] img')
    if not img:
        img = infobox.select_one("div.section.images img")
    return _image_from_tag(img)


def _parse_shop_coins(td: Tag) -> list[dict[str, str]]:
    coins: list[dict[str, str]] = []
    for coin_span in td.select("span.coin"):
        for cls in COIN_SPECS:
            amt_el = coin_span.select_one(f"span.{cls}")
            if not amt_el:
                continue
            match = re.search(r"(\d+)", _clean_text(amt_el.get_text()))
            if match:
                coins.append({"type": cls, "amount": match.group(1)})
            break
    return coins


def _parse_item_icon_cell(td: Tag) -> str:
    img = td.select_one("span.i img")
    fn = _image_from_tag(img)
    if fn:
        return fn
    a = td.select_one("span.i a[href*='File:'], span.i a[href*='/wiki/File:']")
    return _portrait_from_link(a) if a else ""


def _parse_dotlist_entries(td: Tag) -> list[dict[str, str]]:
    if td.select_one(".na"):
        return [{"name": "无"}]
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for span in td.select("span.i"):
        a = span.select_one("a[title]")
        if not a:
            continue
        name = _clean_text(a.get("title", ""))
        if not name or name in seen:
            continue
        seen.add(name)
        entry: dict[str, str] = {"name": name}
        fn = _image_from_tag(span.select_one("img"))
        if fn:
            entry["image"] = fn
        entries.append(entry)
    return entries


def _parse_npc_spawn_detail(soup: BeautifulSoup) -> str:
    root = soup.select_one(".mw-parser-output")
    if not root:
        return ""

    parts: list[str] = []
    blockquote = root.select_one("blockquote.quotation")
    if blockquote:
        sib = blockquote.find_next_sibling()
        while sib:
            if isinstance(sib, Tag):
                if sib.name in ("h2", "div") and sib.get("id") in ("toc", None) and sib.get("class") and "toc" in sib.get("class", []):
                    break
                if sib.name == "h2":
                    break
                if sib.name == "p":
                    text = _cell_text(sib)
                    if text and any(k in text for k in _SPAWN_INTRO_KEYS):
                        parts.append(text.rstrip("：:"))
                    elif parts:
                        break
                if sib.name == "ul" and parts:
                    for li in sib.find_all("li", recursive=False):
                        line = _cell_text(li)
                        if line:
                            parts.append(line)
                    break
            sib = sib.find_next_sibling()

    if parts:
        return "\n".join(parts)

    h2 = _find_section_h2(soup, "生成条件")
    if not h2:
        return ""
    for sib in h2.find_next_siblings():
        if isinstance(sib, Tag) and sib.name == "h2":
            break
        if isinstance(sib, Tag) and sib.name == "p":
            text = _cell_text(sib)
            if text and not text.startswith("以下是符合条件"):
                return text
        if isinstance(sib, Tag) and sib.name == "table":
            break
    return ""


def _parse_coin_price(td: Tag) -> str:
    coin = td.select_one("span.coin")
    if coin and coin.get("title"):
        return _clean_text(coin["title"])
    return _cell_text(td)


def _parse_npc_shop(soup: BeautifulSoup) -> list[dict[str, str]] | None:
    h2 = _find_section_h2(soup, "出售物品")
    if not h2:
        return None
    table = h2.find_next("table", class_=lambda c: c and "terraria" in c and "lined" in c)
    if not table:
        return None

    items: list[dict[str, str]] = []
    for tr in table.select("tbody tr"):
        if tr.find("th"):
            continue
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        name_a = tds[0].select_one("a[title]")
        name = _clean_text(name_a["title"]) if name_a else _cell_text(tds[0])
        if not name:
            continue
        entry: dict[str, str] = {
            "name": name,
            "image": _parse_item_icon_cell(tds[0]),
            "price": _parse_coin_price(tds[1]),
        }
        coins = _parse_shop_coins(tds[1])
        if coins:
            entry["coins"] = coins
        if len(tds) > 2:
            availability = _cell_text(tds[2])
            if availability:
                entry["availability"] = availability
        items.append(entry)
    return items or None


def _dotlist_names(td: Tag) -> list[str]:
    return [e["name"] for e in _parse_dotlist_entries(td)]


def _parse_npc_preferences(soup: BeautifulSoup) -> list[dict[str, Any]] | None:
    h2 = _find_section_h2(soup, "生活偏好")
    if not h2:
        return None
    table = h2.find_next("table", class_="living-preferences")
    if not table:
        return None

    rows: list[dict[str, Any]] = []
    for tr in table.select("tr.love, tr.like, tr.dislike, tr.hate"):
        th = tr.find("th")
        if not th:
            continue
        level = _clean_text(th.get_text())
        tds = tr.find_all("td", recursive=False)
        biomes = _parse_dotlist_entries(tds[0]) if tds else []
        neighbors = _parse_dotlist_entries(tds[1]) if len(tds) > 1 else []
        if not biomes and tds and tds[0].select_one(".na"):
            biomes = [{"name": "无"}]
        if not neighbors and len(tds) > 1 and tds[1].select_one(".na"):
            neighbors = [{"name": "无"}]
        rows.append({"level": level, "biomes": biomes, "neighbors": neighbors})
    return rows or None


def _parse_npc_shimmer(soup: BeautifulSoup) -> tuple[str, str]:
    h2 = _find_section_h2(soup, "微光形态")
    if not h2:
        return "", ""
    image = ""
    box = h2.find_next("div", class_="npcshimmeredform")
    if box:
        img = box.select_one(".mainimage img")
        if img and img.get("src"):
            image = _filename_from_url(_image_url_from_src(img["src"]))
    text = ""
    for sib in h2.find_next_siblings():
        if isinstance(sib, Tag) and sib.name == "h2":
            break
        if isinstance(sib, Tag) and sib.name == "p":
            candidate = _cell_text(sib)
            if candidate and "微光" in candidate:
                text = candidate
                break
    return text, image


def parse_npc_page_html(
    html: str,
    *,
    wiki_title: str,
    catalog_entry: dict[str, str] | None = None,
) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    infobox = soup.select_one("div.infobox.npc")
    if not infobox:
        return None

    catalog_entry = catalog_entry or {}
    name = _clean_text(infobox.select_one("div.title").get_text()) if infobox.select_one("div.title") else wiki_title
    sprite = _parse_npc_sprite(infobox) or catalog_entry.get("sprite", "") or catalog_entry.get("portrait", "")

    description = catalog_entry.get("description") or ""
    spawn = _parse_npc_spawn_detail(soup) or catalog_entry.get("spawn_overview", "")
    shop = _parse_npc_shop(soup)
    preferences = _parse_npc_preferences(soup)
    shimmer, shimmer_image = _parse_npc_shimmer(soup)

    if not sprite and not description and not spawn:
        return None

    item: dict[str, Any] = {
        "name": name or wiki_title,
        "wiki_title": wiki_title,
        "page_type": "npc",
        "image": sprite,
    }
    if catalog_entry.get("phase"):
        item["phase"] = catalog_entry["phase"]
    if description:
        item["description"] = description
    if spawn:
        item["spawn"] = spawn
    if shop:
        item["shop"] = shop
    if preferences:
        item["preferences"] = preferences
    if shimmer:
        item["shimmer"] = shimmer
    if shimmer_image:
        item["shimmer_image"] = shimmer_image
    return item


def parse_npc_page_file(
    wiki_title: str,
    *,
    catalog_entry: dict[str, str] | None = None,
    path: str | None = None,
) -> dict | None:
    page_path = path or _npc_page_html_path(wiki_title)
    if not os.path.isfile(page_path):
        return None
    html = open(page_path, "r", encoding="utf-8").read()
    return parse_npc_page_html(html, wiki_title=wiki_title, catalog_entry=catalog_entry)


def build_npcs_from_mirror(
    catalog: list[dict[str, str]] | None = None,
) -> dict[str, dict]:
    catalog = catalog if catalog is not None else load_npc_catalog_from_overview()
    catalog_by_title = {entry["wiki_title"]: entry for entry in catalog}
    npcs: dict[str, dict] = {}
    for entry in catalog:
        wiki_title = entry["wiki_title"]
        parsed = parse_npc_page_file(wiki_title, catalog_entry=entry)
        if not parsed:
            continue
        key = parsed.get("name") or wiki_title
        npcs[key] = parsed
    for title in catalog_by_title:
        if title not in {v.get("wiki_title") for v in npcs.values()}:
            parsed = parse_npc_page_file(title, catalog_entry=catalog_by_title[title])
            if parsed:
                npcs[parsed.get("name") or title] = parsed
    return npcs


def load_npcs_for_plugin(categories_dir: str = CATEGORIES_DIR) -> dict[str, dict]:
    path = os.path.join(categories_dir, "npcs.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _collect_npc_image_urls(npcs: dict[str, dict]) -> dict[str, str]:
    urls: dict[str, str] = {}

    def add(fn: str | None) -> None:
        if fn:
            urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"

    for npc in npcs.values():
        for field in ("image", "shimmer_image"):
            add(npc.get(field))
        for shop_item in npc.get("shop") or []:
            add(shop_item.get("image"))
        for pref in npc.get("preferences") or []:
            for entry in pref.get("biomes") or []:
                if isinstance(entry, dict):
                    add(entry.get("image"))
            for entry in pref.get("neighbors") or []:
                if isinstance(entry, dict):
                    add(entry.get("image"))
    return urls


def _patch_manifest_npc_count(count: int, categories_dir: str = CATEGORIES_DIR) -> None:
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

    npc_entry = {
        "key": "npcs",
        "label": "NPC",
        "file": "npcs.json",
        "count": count,
    }
    old_count = 0
    updated = False
    for i, cat in enumerate(categories):
        if cat.get("key") == "npcs":
            old_count = int(cat.get("count") or 0)
            categories[i] = npc_entry
            updated = True
            break
    if not updated:
        categories.append(npc_entry)

    manifest["categories"] = categories
    old_total = manifest.get("total", 0)
    if isinstance(old_total, int):
        manifest["total"] = old_total - old_count + count

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _load_existing_npcs() -> dict[str, dict]:
    return load_npcs_for_plugin(CATEGORIES_DIR)


async def refresh_npcs(
    session: aiohttp.ClientSession,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """从本地 Wiki 镜像抓取城镇 NPC 并写入 categories/npcs.json。"""
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

    catalog = load_npc_catalog_from_overview()
    if not catalog:
        return {"new_count": 0, "total": len(_load_existing_npcs()), "error": "no_catalog"}

    catalog_by_title = {entry["wiki_title"]: entry for entry in catalog}
    npcs: dict[str, dict] = {} if force else _load_existing_npcs()
    before = len(npcs)
    new_count = 0

    for entry in catalog:
        wiki_title = entry["wiki_title"]
        key = entry.get("label") or wiki_title
        if not force and key in npcs:
            continue
        parsed = parse_npc_page_file(wiki_title, catalog_entry=catalog_by_title.get(wiki_title))
        if not parsed:
            continue
        npcs[key] = parsed
        new_count += 1

    image_urls = _collect_npc_image_urls(npcs)
    images_total = len(image_urls)
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

    with open(NPCS_JSON, "w", encoding="utf-8") as f:
        json.dump(npcs, f, ensure_ascii=False, indent=2)

    _patch_manifest_npc_count(len(npcs))

    return {
        "new_count": new_count if force else max(0, len(npcs) - before),
        "total": len(npcs),
        "images_ok": images_ok,
        "images_total": images_total,
        "catalog_size": len(catalog),
    }


def ingest_npcs_local(*, force: bool = False) -> dict[str, Any]:
    """仅使用本地镜像构建 npcs.json（不下载图片）。"""
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    npcs = build_npcs_from_mirror()
    if not force:
        existing = _load_existing_npcs()
        for key, value in existing.items():
            npcs.setdefault(key, value)

    with open(NPCS_JSON, "w", encoding="utf-8") as f:
        json.dump(npcs, f, ensure_ascii=False, indent=2)

    _patch_manifest_npc_count(len(npcs))
    return {"total": len(npcs), "catalog_size": len(load_npc_catalog_from_overview())}
