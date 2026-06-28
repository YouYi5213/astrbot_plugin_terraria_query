"""泰拉瑞亚 Boss：概览目录解析、多难度属性/掉落与 bosses.json 持久化。"""

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
        _parse_mode_field,
        _resolve_mode_values,
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
        _parse_mode_field,
        _resolve_mode_values,
        download_image,
    )

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_PLUGIN_DIR, "data", "terraria_query")
CATEGORIES_DIR = os.path.join(DATA_DIR, "categories")
BOSSES_JSON = os.path.join(CATEGORIES_DIR, "bosses.json")
MANIFEST_JSON = os.path.join(CATEGORIES_DIR, "manifest.json")

BOSS_OVERVIEW_PAGE = "Bosses"
_BOSS_SKIP_STAT_LABELS = frozenset({"免疫", "Immunities"})
_MODE_ORDER = ("normal", "expert", "master")
_MODE_LABELS = {"normal": "经典", "expert": "专家", "master": "大师"}


def _overview_html_path() -> str:
    return os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{BOSS_OVERVIEW_PAGE}.html")


def _boss_page_html_path(wiki_title: str) -> str:
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
    for sound in node.select("span.sound, audio"):
        sound.decompose()
    sep = "\n" if multiline else " "
    text = node.get_text(sep, strip=True)
    if multiline:
        text = re.sub(r"\n{2,}", "\n", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return _clean_text(text)


def _image_from_tag(img: Tag | None) -> str:
    if img and img.get("src"):
        return _filename_from_url(_image_url_from_src(img["src"]))
    return ""


def _parse_item_from_span(span: Tag | None) -> tuple[str, str]:
    if not span:
        return "", ""
    a = span.select_one("a[title]")
    name = _clean_text(a.get("title", "")) if a else ""
    if not name:
        name = _clean_text(span.get_text())
    img = span.select_one("img")
    return name, _image_from_tag(img)


def _section_h2(soup: BeautifulSoup, section_id: str) -> Tag | None:
    anchor = soup.find(id=section_id)
    if not anchor:
        return None
    if anchor.name == "h2":
        return anchor
    return anchor.find_parent("h2")


def _category_from_h2(h2: Tag | None) -> str:
    if not h2:
        return "other"
    headline = h2.find(class_="mw-headline")
    section_id = headline.get("id", "") if headline else ""
    if "困难模式之前" in section_id or "Pre-Hardmode" in section_id:
        return "pre_hardmode"
    if "困难模式" in section_id and "之前" not in section_id:
        return "hardmode"
    if "事件" in section_id:
        return "event"
    if "特殊" in section_id or "种子" in section_id:
        return "special"
    return "other"


def load_boss_catalog_from_overview(path: str | None = None) -> list[dict[str, str]]:
    """从 Bosses.html 读取 Boss 目录。"""
    html_path = path or _overview_html_path()
    if not os.path.isfile(html_path):
        return []

    soup = BeautifulSoup(open(html_path, "r", encoding="utf-8").read(), "html.parser")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    current_category = "other"

    root = soup.select_one(".mw-parser-output")
    if not root:
        return entries

    for h3 in root.find_all("h3"):
        headline = h3.find(class_="mw-headline")
        if not headline:
            continue
        label = _clean_text(headline.get_text())
        current_category = _category_from_h2(h3.find_previous("h2"))
        card = h3.find_next("div", class_="infocard")
        if not card:
            continue
        link = card.select_one(".minicard a[title]")
        if not link:
            continue
        wiki_title = _clean_text(link.get("title", ""))
        if not wiki_title or wiki_title in seen:
            continue
        img = card.select_one(".minicard img")
        seen.add(wiki_title)
        entries.append(
            {
                "wiki_title": wiki_title,
                "label": label or wiki_title,
                "image": _image_from_tag(img),
                "category": current_category,
            }
        )
    return entries


def _li_matches_mode(classes: set[str], mode: str) -> bool:
    ignored = {
        "",
        "group",
        "loot",
        "groupstart",
        "groupend",
        "lootstart",
        "lootend",
        "caption",
    }
    effective = classes - ignored
    if not effective:
        return True
    if mode == "normal":
        return bool(effective & {"m-normal", "m-journey"})
    if mode == "expert":
        return bool(effective & {"m-expert", "m-expert-master"})
    if mode == "master":
        return bool(effective & {"m-master", "m-expert-master"})
    return False


def _parse_coins_from_node(node: Tag) -> list[dict[str, str]]:
    coins: list[dict[str, str]] = []
    for coin_span in node.select("span.coin"):
        for cls, image in COIN_SPECS.items():
            amt_el = coin_span.select_one(f"span.{cls}")
            if not amt_el:
                continue
            match = re.search(r"(\d+)", _clean_text(amt_el.get_text()))
            if match:
                coins.append({"type": cls, "amount": match.group(1), "image": image})
    return coins


def _parse_boss_money(table: Tag | None) -> dict[str, list[dict[str, str]]]:
    if not table:
        return {}
    td = table.select_one("td")
    if not td:
        return {}

    result: dict[str, list[dict[str, str]]] = {mode: [] for mode in _MODE_ORDER}
    mode_content = td.select_one("span.mode-content")
    if mode_content:
        for span in mode_content.find_all("span", recursive=False):
            classes = set(span.get("class") or [])
            coins = _parse_coins_from_node(span)
            if not coins:
                continue
            if classes & {"m-normal", "m-journey"}:
                result["normal"] = coins
            if "m-expert-master" in classes:
                result["expert"] = list(coins)
                result["master"] = list(coins)
            elif "m-expert" in classes:
                result["expert"] = coins
            elif "m-master" in classes:
                result["master"] = coins
        return result

    coins = _parse_coins_from_node(td)
    if coins:
        return {mode: list(coins) for mode in _MODE_ORDER}
    return result


def _parse_boss_drops_list(ul: Tag | None) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {m: [] for m in _MODE_ORDER}
    if not ul:
        return result

    for li in ul.select(":scope > li"):
        classes = set(li.get("class") or [])
        if any(x in classes for x in ("groupend", "lootend", "lootstart")):
            continue
        if li.select("strong") and not li.select("span.i"):
            continue
        if "groupstart" in classes or ("caption" in classes and "group" in classes):
            caption = _cell_text(li)
            for mode in _MODE_ORDER:
                if _li_matches_mode(classes, mode):
                    result[mode].append({"type": "caption", "text": caption})
            continue
        divs = li.select(":scope > div")
        if len(divs) < 2:
            continue
        name, image = _parse_item_from_span(divs[0].select_one("span.i"))
        if not name:
            continue
        qty = ""
        qty_span = divs[0].select_one("span.nowrap")
        if qty_span:
            qty = _clean_text(qty_span.get_text())
        chance = _cell_text(divs[1])
        note_el = divs[0].select_one("span.note")
        note = _clean_text(note_el.get_text()) if note_el else ""
        entry: dict[str, Any] = {
            "type": "item",
            "name": name,
            "image": image,
            "quantity": qty,
            "chance": chance,
        }
        if note:
            entry["note"] = note
        for mode in _MODE_ORDER:
            if _li_matches_mode(classes, mode):
                result[mode].append(dict(entry))
    return result


def _parse_npcstat_from_tag(npcstat: Tag) -> dict[str, str]:
    values: dict[str, str] = {}
    for child in npcstat.children:
        if not isinstance(child, Tag) or child.name != "span":
            continue
        classes = set(child.get("class") or [])
        if "ssep" in classes:
            continue
        text = _clean_text(child.get_text())
        if not text:
            continue
        if "m-all" in classes:
            return {mode: text for mode in _MODE_ORDER}
        if classes & {"m-normal", "m-journey"}:
            values["normal"] = text
        elif "m-expert" in classes:
            values["expert"] = text
        elif "m-master" in classes:
            values["master"] = text
    if values:
        return {
            "normal": values.get("normal", ""),
            "expert": values.get("expert", values.get("normal", "")),
            "master": values.get("master", values.get("expert", values.get("normal", ""))),
        }
    text = _cell_text(npcstat)
    return {"normal": text, "expert": text, "master": text}


def _append_mode_content_span(parts: dict[str, list[str]], span: Tag) -> None:
    classes = set(span.get("class") or [])
    text = _cell_text(span)
    if not text:
        return
    if classes & {"m-normal", "m-journey"}:
        parts["normal"].append(text)
    elif "m-expert-master" in classes:
        parts["expert"].append(text)
        parts["master"].append(text)
    elif "m-expert" in classes:
        parts["expert"].append(text)
    elif "m-master" in classes:
        parts["master"].append(text)


def _parse_mode_content_block(parts: dict[str, list[str]], block: Tag) -> None:
    for child in block.children:
        if isinstance(child, Tag) and child.name == "span":
            _append_mode_content_span(parts, child)
            continue
        text = _clean_text(str(child))
        if text:
            _append_trailing_text(parts, text)


def _append_note_to_parts(parts: dict[str, list[str]], note: str) -> None:
    if not note:
        return
    for mode in _MODE_ORDER:
        if parts[mode]:
            parts[mode][-1] = f"{parts[mode][-1]} {note}".strip()
        else:
            parts[mode].append(note)


def _append_trailing_text(parts: dict[str, list[str]], text: str) -> None:
    if not text:
        return
    for mode in reversed(_MODE_ORDER):
        if parts[mode]:
            parts[mode].append(text)
            return
    parts["master"].append(text)


def _append_trailing_text_active_modes(parts: dict[str, list[str]], text: str) -> None:
    if not text:
        return
    active = [mode for mode in _MODE_ORDER if parts[mode]]
    if not active:
        parts["master"].append(text)
        return
    for mode in active:
        parts[mode].append(text)


def _finalize_mode_parts(parts: dict[str, list[str]]) -> dict[str, str]:
    return {mode: "\n".join(line for line in parts[mode] if line).strip() for mode in _MODE_ORDER}


def _parse_npcstat_modes(td: Tag) -> dict[str, str]:
    npcstat = td.select_one("span.npcstat")
    if not npcstat:
        resolved = _resolve_mode_values(td)
        if resolved:
            return resolved
        text = _cell_text(td, multiline=True)
        return {"normal": text, "expert": text, "master": text}
    return _parse_npcstat_from_tag(npcstat)


def _parse_mode_text_lines(td: Tag) -> dict[str, str]:
    clone = BeautifulSoup(str(td), "lxml")
    node = clone.select_one("td") or clone
    _strip_refs(node)
    for sup in node.select("sup.reference"):
        sup.decompose()

    npcstat = node.select_one("span.npcstat")
    mode_blocks = node.select("span.mode-content")
    notes = node.select("span.note-text")

    if npcstat and not mode_blocks and not notes:
        return _parse_npcstat_from_tag(npcstat)

    parts: dict[str, list[str]] = {mode: [] for mode in _MODE_ORDER}
    if npcstat:
        base = _parse_npcstat_from_tag(npcstat)
        for mode, value in base.items():
            if value:
                parts[mode].append(value)

    seen_mode_content = False
    for child in node.children:
        if isinstance(child, Tag):
            classes = set(child.get("class") or [])
            if "npcstat" in classes:
                continue
            if "mode-content" in classes:
                seen_mode_content = True
                _parse_mode_content_block(parts, child)
            elif "note-text" in classes:
                note = _cell_text(child)
                if seen_mode_content:
                    _append_trailing_text_active_modes(parts, note)
                elif parts["master"]:
                    parts["master"][-1] = f"{parts['master'][-1]} {note}".strip()
                else:
                    _append_note_to_parts(parts, note)
            continue
        text = _clean_text(str(child))
        if text:
            if seen_mode_content:
                _append_trailing_text_active_modes(parts, text)
            else:
                _append_trailing_text(parts, text)

    if not any(parts[mode] for mode in _MODE_ORDER):
        resolved = _parse_npcstat_modes(node)
        if any(resolved.get(mode) for mode in _MODE_ORDER):
            return resolved
        mode_content = _parse_mode_field(node)
        if mode_content:
            return {
                "normal": mode_content.get("normal", ""),
                "expert": mode_content.get("expert", mode_content.get("normal", "")),
                "master": mode_content.get("master", mode_content.get("expert", "")),
            }
        plain = _cell_text(node, multiline=True)
        return {"normal": plain, "expert": plain, "master": plain}

    return _finalize_mode_parts(parts)


def _parse_uniform_or_mode_cell(td: Tag) -> dict[str, str]:
    if td.select_one("span.npcstat, span.mode-content, span.note-text"):
        return _parse_mode_text_lines(td)
    text = _cell_text(td)
    if not text:
        return {mode: "" for mode in _MODE_ORDER}
    return {mode: text for mode in _MODE_ORDER}


def _parse_boss_stats_table(table: Tag) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tr in table.select("tr"):
        th, td = tr.select_one("th"), tr.select_one("td")
        if not th or not td:
            continue
        label = _clean_text(th.get_text())
        if not label or label in _BOSS_SKIP_STAT_LABELS:
            continue
        modes = _parse_mode_text_lines(td)
        rows.append({"label": label, "modes": modes})
    return rows


def _parse_boss_debuff_section(section: Tag | None) -> dict[str, Any] | None:
    if not section:
        return None
    table = section.select_one("table.stat")
    if not table:
        return None

    debuff: dict[str, Any] = {}
    for tr in table.select("tr"):
        th, td = tr.select_one("th"), tr.select_one("td")
        if not th or not td:
            continue
        label = _clean_text(th.get_text())
        if label == "减益":
            name, image = _parse_item_from_span(td.select_one("span.i"))
            if not name:
                name = _cell_text(td)
            debuff["name"] = name
            if image:
                debuff["image"] = image
        elif label == "减益描述":
            debuff["description"] = _cell_text(td)
        elif label == "几率":
            debuff["chance"] = _parse_uniform_or_mode_cell(td)
        elif label == "持续时间":
            debuff["duration"] = _parse_uniform_or_mode_cell(td)

    if not debuff.get("name"):
        return None
    return debuff


def _parse_boss_infobox(infobox: Tag, *, include_drops: bool = True) -> dict[str, Any]:
    title_el = infobox.select_one("div.title")
    name = _clean_text(title_el.get_text()) if title_el else ""
    subtitle_el = infobox.select_one(".namesub")
    subtitle = _clean_text(subtitle_el.get_text()) if subtitle_el else ""

    img = infobox.select_one("div.section.images img")
    image = _image_from_tag(img)

    stats: list[dict[str, Any]] = []
    stat_section = infobox.select_one("div.section.statistics")
    if stat_section:
        table = stat_section.select_one("table.stat")
        if table:
            stats = _parse_boss_stats_table(table)

    debuff = _parse_boss_debuff_section(infobox.select_one("div.section.debuff"))

    drops: dict[str, Any] = {}
    if include_drops:
        drops_section = infobox.select_one("div.section.drops")
        if drops_section:
            money = _parse_boss_money(drops_section.select_one("table.drops.money"))
            items_by_mode = _parse_boss_drops_list(drops_section.select_one("ul.drops.items"))
            drops = {"money": money, "items": items_by_mode}

    return {
        "name": name,
        "subtitle": subtitle,
        "image": image,
        "stats": stats,
        "debuff": debuff,
        "drops": drops,
    }


def _parse_boss_spawn(soup: BeautifulSoup) -> str:
    h2 = _section_h2(soup, "召唤和生成") or _section_h2(soup, "召唤")
    if not h2:
        return ""
    parts: list[str] = []
    for sib in h2.find_next_siblings():
        if isinstance(sib, Tag) and sib.name == "h2":
            break
        if isinstance(sib, Tag) and sib.name == "p":
            text = _cell_text(sib)
            if text:
                parts.append(text)
        if isinstance(sib, Tag) and sib.name == "h3":
            break
    return "\n".join(parts[:2])


def _parse_boss_description(soup: BeautifulSoup) -> str:
    root = soup.select_one(".mw-parser-output")
    if not root:
        return ""
    started = False
    parts: list[str] = []
    for child in root.children:
        if not isinstance(child, Tag):
            continue
        if child.select_one("div.infobox.npc"):
            started = True
            continue
        if not started:
            continue
        if child.get("id") == "toc" or (child.get("class") and "toc" in child.get("class", [])):
            break
        if child.name == "h2":
            break
        if child.name == "p":
            text = _cell_text(child)
            if text:
                parts.append(text)
    return "\n\n".join(parts[:2])


def _parse_boss_flavor(soup: BeautifulSoup) -> str:
    flavor = soup.select_one("div.flavor-text")
    if not flavor:
        return ""
    text = _cell_text(flavor)
    text = re.sub(r"^[""「]|[""」]$", "", text.strip())
    return text


def _is_boss_part_infobox(infobox: Tag) -> bool:
    table = infobox.select_one("div.section.statistics table.stat")
    if not table:
        return False
    for tr in table.select("tr"):
        th = tr.select_one("th")
        if not th or _clean_text(th.get_text()) != "类型":
            continue
        td = tr.select_one("td")
        text = _cell_text(td) if td else ""
        return "Boss部位" in text.replace(" ", "")
    return False


def _part_display_name(parsed: dict[str, Any], boss_label: str) -> str:
    subtitle = (parsed.get("subtitle") or "").strip()
    name = (parsed.get("name") or "").strip()
    if subtitle and subtitle != name:
        return subtitle
    if name and name != boss_label:
        return name
    return subtitle or name


def _parse_boss_inline_parts(
    main_infobox: Tag,
    *,
    boss_label: str,
) -> list[dict[str, Any]]:
    """解析紧挨主 infobox 之后、正文前的 Boss 部位（如骷髅王之手）。"""
    parts: list[dict[str, Any]] = []
    for sib in main_infobox.find_next_siblings():
        if not isinstance(sib, Tag):
            continue
        if sib.name == "h2" or sib.get("id") == "toc":
            break
        classes = sib.get("class") or []
        if sib.name == "div" and "flavor-text" in classes:
            break
        if (
            sib.name == "div"
            and "infobox" in classes
            and "npc" in classes
            and "modesbox" in classes
        ):
            if not _is_boss_part_infobox(sib):
                continue
            parsed = _parse_boss_infobox(sib, include_drops=False)
            display = _part_display_name(parsed, boss_label)
            if not display:
                continue
            parsed["name"] = display
            parts.append(parsed)
            continue
        if sib.name in ("h2", "h3", "p"):
            break
    return parts


def _parse_boss_parts(
    soup: BeautifulSoup,
    *,
    main_infobox: Tag | None = None,
    boss_label: str = "",
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    seen: set[str] = set()

    h2 = _section_h2(soup, "部位")
    if h2:
        wrapper = h2.find_next("div", class_="infobox-wrapper")
        if wrapper:
            for box in wrapper.select("div.infobox.npc"):
                parsed = _parse_boss_infobox(box, include_drops=False)
                display = _part_display_name(parsed, boss_label) if boss_label else parsed.get("name", "")
                if not display or display in seen:
                    continue
                parsed["name"] = display
                seen.add(display)
                parts.append(parsed)

    if main_infobox is not None:
        for parsed in _parse_boss_inline_parts(main_infobox, boss_label=boss_label):
            display = parsed.get("name", "")
            if display and display not in seen:
                seen.add(display)
                parts.append(parsed)

    return parts


def parse_boss_page_html(
    html: str,
    *,
    wiki_title: str,
    catalog_entry: dict[str, str] | None = None,
) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    infobox = soup.select_one(".mw-parser-output > div.infobox.npc.modesbox")
    if not infobox:
        infobox = soup.select_one("div.infobox.npc.modesbox")
    if not infobox:
        return None

    catalog_entry = catalog_entry or {}
    main = _parse_boss_infobox(infobox, include_drops=True)
    name = catalog_entry.get("label") or wiki_title
    image = main.get("image") or catalog_entry.get("image", "")

    boss: dict[str, Any] = {
        "name": name,
        "wiki_title": wiki_title,
        "page_type": "boss",
        "image": image,
        "stats": main.get("stats") or [],
        "drops": main.get("drops") or {},
    }
    if main.get("debuff"):
        boss["debuff"] = main["debuff"]
    if main.get("subtitle"):
        boss["subtitle"] = main["subtitle"]
    if catalog_entry.get("category"):
        boss["category"] = catalog_entry["category"]

    flavor = _parse_boss_flavor(soup)
    if flavor:
        boss["flavor"] = flavor
    description = _parse_boss_description(soup)
    if description:
        boss["description"] = description
    spawn = _parse_boss_spawn(soup)
    if spawn:
        boss["spawn"] = spawn

    parts = _parse_boss_parts(soup, main_infobox=infobox, boss_label=name)
    if parts:
        boss["parts"] = parts

    if not boss.get("stats") and not boss.get("drops") and not image:
        return None
    return boss


def parse_boss_page_file(
    wiki_title: str,
    *,
    catalog_entry: dict[str, str] | None = None,
    path: str | None = None,
) -> dict | None:
    page_path = path or _boss_page_html_path(wiki_title)
    if not os.path.isfile(page_path):
        return None
    html = open(page_path, "r", encoding="utf-8").read()
    return parse_boss_page_html(html, wiki_title=wiki_title, catalog_entry=catalog_entry)


def build_bosses_from_mirror(
    catalog: list[dict[str, str]] | None = None,
) -> dict[str, dict]:
    catalog = catalog if catalog is not None else load_boss_catalog_from_overview()
    catalog_by_title = {entry["wiki_title"]: entry for entry in catalog}
    bosses: dict[str, dict] = {}
    for entry in catalog:
        wiki_title = entry["wiki_title"]
        parsed = parse_boss_page_file(wiki_title, catalog_entry=entry)
        if not parsed:
            continue
        key = entry.get("label") or wiki_title
        parsed["name"] = key
        bosses[key] = parsed
    for title, entry in catalog_by_title.items():
        key = entry.get("label") or title
        if key in bosses:
            continue
        parsed = parse_boss_page_file(title, catalog_entry=entry)
        if parsed:
            parsed["name"] = key
            bosses[key] = parsed
    return bosses


def load_bosses_for_plugin(categories_dir: str = CATEGORIES_DIR) -> dict[str, dict]:
    path = os.path.join(categories_dir, "bosses.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _collect_boss_image_urls(bosses: dict[str, dict]) -> dict[str, str]:
    urls: dict[str, str] = {}

    def add(fn: str | None) -> None:
        if fn:
            urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"

    for boss in bosses.values():
        add(boss.get("image"))
        add((boss.get("debuff") or {}).get("image"))
        for part in boss.get("parts") or []:
            add(part.get("image"))
            add((part.get("debuff") or {}).get("image"))
        drops = boss.get("drops") or {}
        for mode_items in (drops.get("items") or {}).values():
            for entry in mode_items:
                if isinstance(entry, dict) and entry.get("type") == "item":
                    add(entry.get("image"))
    return urls


def _patch_manifest_boss_count(count: int, categories_dir: str = CATEGORIES_DIR) -> None:
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

    boss_entry = {
        "key": "bosses",
        "label": "Boss",
        "file": "bosses.json",
        "count": count,
    }
    old_count = 0
    updated = False
    for i, cat in enumerate(categories):
        if cat.get("key") == "bosses":
            old_count = int(cat.get("count") or 0)
            categories[i] = boss_entry
            updated = True
            break
    if not updated:
        categories.append(boss_entry)

    manifest["categories"] = categories
    old_total = manifest.get("total", 0)
    if isinstance(old_total, int):
        manifest["total"] = old_total - old_count + count

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _load_existing_bosses() -> dict[str, dict]:
    return load_bosses_for_plugin(CATEGORIES_DIR)


async def refresh_bosses(
    session: aiohttp.ClientSession,
    *,
    force: bool = False,
) -> dict[str, Any]:
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

    catalog = load_boss_catalog_from_overview()
    if not catalog:
        return {"new_count": 0, "total": len(_load_existing_bosses()), "error": "no_catalog"}

    catalog_by_title = {entry["wiki_title"]: entry for entry in catalog}
    bosses: dict[str, dict] = {} if force else _load_existing_bosses()
    before = len(bosses)
    new_count = 0

    for entry in catalog:
        wiki_title = entry["wiki_title"]
        key = entry.get("label") or wiki_title
        if not force and key in bosses:
            continue
        parsed = parse_boss_page_file(wiki_title, catalog_entry=catalog_by_title.get(wiki_title))
        if not parsed:
            continue
        parsed["name"] = key
        bosses[key] = parsed
        new_count += 1

    image_urls = _collect_boss_image_urls(bosses)
    images_total = len(image_urls)
    images_ok = 0
    if image_urls:
        pending = {
            fn: url
            for fn, url in image_urls.items()
            if fn and not os.path.isfile(os.path.join(IMAGES_DIR, fn))
        }
        if pending:
            semaphore = asyncio.Semaphore(8)
            results = await asyncio.gather(
                *[
                    download_image(session, fn, url, semaphore)
                    for fn, url in pending.items()
                ]
            )
            images_ok = sum(1 for r in results if r)
        images_total = len(pending) if pending else 0

    with open(BOSSES_JSON, "w", encoding="utf-8") as f:
        json.dump(bosses, f, ensure_ascii=False, indent=2)

    _patch_manifest_boss_count(len(bosses))

    return {
        "new_count": new_count if force else max(0, len(bosses) - before),
        "total": len(bosses),
        "images_ok": images_ok,
        "images_total": images_total,
        "catalog_size": len(catalog),
    }


async def backfill_boss_images(
    session: aiohttp.ClientSession,
    bosses: dict[str, dict] | None = None,
) -> dict[str, int]:
    """下载 bosses.json 中引用但 images/ 目录缺失的图片。"""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    bosses = bosses if bosses is not None else _load_existing_bosses()
    image_urls = _collect_boss_image_urls(bosses)
    pending = {
        fn: url
        for fn, url in image_urls.items()
        if fn and not os.path.isfile(os.path.join(IMAGES_DIR, fn))
    }
    if not pending:
        return {"images_ok": 0, "images_total": 0}

    semaphore = asyncio.Semaphore(8)
    results = await asyncio.gather(
        *[
            download_image(session, fn, url, semaphore)
            for fn, url in pending.items()
        ]
    )
    return {
        "images_ok": sum(1 for r in results if r),
        "images_total": len(pending),
    }


def ingest_bosses_local(*, force: bool = False, download_images: bool = True) -> dict[str, Any]:
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    bosses = build_bosses_from_mirror()
    if not force:
        existing = _load_existing_bosses()
        for key, value in existing.items():
            bosses.setdefault(key, value)

    with open(BOSSES_JSON, "w", encoding="utf-8") as f:
        json.dump(bosses, f, ensure_ascii=False, indent=2)

    _patch_manifest_boss_count(len(bosses))
    result: dict[str, Any] = {
        "total": len(bosses),
        "catalog_size": len(load_boss_catalog_from_overview()),
    }
    if download_images:
        async def _run() -> dict[str, int]:
            connector = aiohttp.TCPConnector(limit=10)
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout
            ) as session:
                return await backfill_boss_images(session, bosses)

        img_result = asyncio.run(_run())
        result.update(img_result)
    return result
