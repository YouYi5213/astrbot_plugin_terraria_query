"""
泰拉瑞亚 Wiki 离线数据准备脚本
================================
一次性运行，从 terraria.wiki.gg 中文 Wiki 爬取物品数据并保存到本地。

用法:
    python prepare_data.py              # 增量更新，仅抓取新增物品
    python prepare_data.py --limit 20   # 调试：仅处理前 20 个新页面
    python prepare_data.py --force      # 全量重建（覆盖已有数据）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from urllib.parse import quote, unquote

import aiohttp
from bs4 import BeautifulSoup, Tag

WIKI_BASE = "https://terraria.wiki.gg/zh"
API_URL = f"{WIKI_BASE}/api.php"
API_URL_EN = "https://terraria.wiki.gg/api.php"
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_PLUGIN_DIR, "data", "terraria_query")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
ITEMS_JSON = os.path.join(DATA_DIR, "items.json")

CATEGORIES = [
    "Category:近战武器",
    "Category:远程武器",
    "Category:魔法武器",
    "Category:召唤武器",
    "Category:武器物品",
    "Category:工具物品",
    "Category:制作材料物品",
    "Category:盔甲物品",
    "Category:盔甲套装",
    "Category:配饰物品",
    "Category:治疗物品",
]

# Wiki 上的物品类型总览页（非单个物品，有独立 infobox + 导语）
OVERVIEW_PAGES: dict[str, dict] = {
    "翅膀": {"aliases": ["Wings"]},
}

OVERVIEW_PAGE_TITLES = frozenset(OVERVIEW_PAGES)
MAX_ITEM_RECIPE_ROWS = 4

HEADERS = {
    "User-Agent": "AstrBot-TerrariaQuery/1.0 (offline data preparation; +https://docs.astrbot.app)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

logger = logging.getLogger(__name__)


def _clean_text(text: str) -> str:
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return text.strip()


def _image_url_from_src(src: str) -> str:
    if src.startswith("http"):
        return src
    if src.startswith("//"):
        return "https:" + src
    return "https://terraria.wiki.gg" + src


def _filename_from_url(url: str) -> str:
    name = unquote(url.split("/")[-1].split("?")[0])
    return name or "unknown.png"


RARITY_LABELS = frozenset({"稀有度", "Rarity"})
SELL_LABELS = frozenset({"卖出", "Sell"})
BUY_LABELS = frozenset({"买入", "Buy"})
COIN_STAT_LABELS = SELL_LABELS | BUY_LABELS

RARITY_SORTKEY_HEX = {
    "00*": "#b8b8b8",
    "01*": "#ffffff",
    "02*": "#5a9cff",
    "03*": "#55dd55",
    "04*": "#ffaa44",
    "05*": "#ff7777",
    "06*": "#ff88bb",
    "07*": "#cc88ff",
    "08*": "#aa55ff",
    "09*": "#dd66ff",
    "10*": "#ff5050",
    "11*": "#ffff66",
    "12*": "#88ff88",
}

RARITY_SORTKEY_ZH = {
    "00*": "灰色",
    "01*": "白色",
    "02*": "蓝色",
    "03*": "绿色",
    "04*": "橙色",
    "05*": "浅红色",
    "06*": "粉红色",
    "07*": "浅紫色",
    "08*": "紫色",
    "09*": "淡紫色",
    "10*": "红色",
    "11*": "黄色",
    "12*": "渐变色",
}

RARITY_SORTKEY_EN = {
    "00*": "Gray",
    "01*": "White",
    "02*": "Blue",
    "03*": "Green",
    "04*": "Orange",
    "05*": "Light Red",
    "06*": "Pink",
    "07*": "Light Purple",
    "08*": "Purple",
    "09*": "Violet",
    "10*": "Red",
    "11*": "Yellow",
    "12*": "Gradient",
}

COIN_SPECS = {
    "pc": "Platinum_Coin.png",
    "gc": "Gold_Coin.png",
    "sc": "Silver_Coin.png",
    "cc": "Copper_Coin.png",
}


def _extract_rarity_sortkey(td) -> str:
    sk = td.select_one("s.sortkey")
    if sk:
        return _clean_text(sk.get_text())
    txt = _clean_text(td.get_text())
    if re.fullmatch(r"\d+\*", txt):
        return txt
    return ""


def _extract_rarity_name_from_title(title: str) -> str:
    if not title:
        return ""
    match = re.match(r"^([^（(]+)", title.strip())
    return match.group(1).strip() if match else ""


def _parse_rarity_stat(td, label: str) -> dict:
    sortkey = _extract_rarity_sortkey(td)
    link = td.select_one(".rarity a") or td.select_one("a")
    title = link.get("title", "") if link else ""
    en_name = _extract_rarity_name_from_title(title)
    if label == "稀有度":
        display = RARITY_SORTKEY_ZH.get(sortkey, en_name or sortkey)
    else:
        display = en_name or RARITY_SORTKEY_EN.get(sortkey, sortkey)
    return {
        "label": label,
        "value": display or _clean_text(td.get_text()),
        "extra": "",
        "sortkey": sortkey,
        "color": RARITY_SORTKEY_HEX.get(sortkey, "#ffffff"),
    }


def _parse_sell_stat(td, label: str) -> dict:
    coins: list[dict] = []
    for cls, image in COIN_SPECS.items():
        el = td.select_one(f"span.{cls}")
        if not el:
            continue
        match = re.search(r"(\d+)", _clean_text(el.get_text()))
        if match:
            coins.append({"type": cls, "amount": match.group(1), "image": image})
    value = "" if coins else _clean_text(td.get_text())
    return {"label": label, "value": value, "extra": "", "coins": coins}


def parse_sell_text_to_coins(text: str) -> list[dict]:
    coins: list[dict] = []
    for match in re.finditer(r"(\d+)\s*(PC|GC|SC|CC)", text, re.I):
        abbr = match.group(2).lower()
        cls = {"pc": "pc", "gc": "gc", "sc": "sc", "cc": "cc"}.get(abbr)
        if cls:
            coins.append(
                {"type": cls, "amount": match.group(1), "image": COIN_SPECS[cls]}
            )
    return coins


def normalize_stat_for_display(stat: dict, locale: str = "zh") -> dict:
    stat = dict(stat)
    label = stat.get("label", "")

    if label in RARITY_LABELS:
        if stat.get("color") and stat.get("value") and "*" not in stat.get("value", ""):
            return stat
        sortkey = stat.get("sortkey") or stat.get("value", "")
        if re.fullmatch(r"\d+\*", sortkey):
            names = RARITY_SORTKEY_ZH if locale == "zh" else RARITY_SORTKEY_EN
            stat["value"] = names.get(sortkey, sortkey)
            stat["color"] = RARITY_SORTKEY_HEX.get(sortkey, "#ffffff")
            stat["sortkey"] = sortkey

    if label in COIN_STAT_LABELS:
        if stat.get("coins"):
            return stat
        coins = parse_sell_text_to_coins(stat.get("value", ""))
        if coins:
            stat["coins"] = coins
            stat["value"] = ""

    return stat


def _parse_stat_value(td) -> tuple[str, str]:
    extra_el = td.select_one(".small-bold, .knockback, .usetime")
    extra = _clean_text(extra_el.get_text(" ", strip=True)) if extra_el else ""
    if extra_el:
        extra_el.extract()
    value = _clean_text(td.get_text())
    if extra:
        extra_core = extra.strip("()（）[] ")
        if extra_core and extra_core in value:
            extra = ""
    return value, extra


def _parse_rich_segments(root) -> list[dict]:
    from bs4 import NavigableString, Tag

    segments: list[dict] = []

    def append_text(text: str) -> None:
        text = _clean_text(text)
        if not text or text in ("(", ")", "（", "）"):
            return
        if segments and segments[-1]["type"] == "text":
            segments[-1]["text"] += text
        else:
            segments.append({"type": "text", "text": text})

    def walk(node) -> None:
        if isinstance(node, NavigableString):
            append_text(str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.name == "br":
            append_text("\n")
            return
        if node.name == "img" and node.get("src"):
            segments.append(
                {
                    "type": "icon",
                    "image": _filename_from_url(_image_url_from_src(node["src"])),
                    "alt": _clean_text(node.get("alt", "")),
                }
            )
            return
        for child in node.children:
            walk(child)

    for child in root.children:
        walk(child)

    cleaned: list[dict] = []
    for i, seg in enumerate(segments):
        if seg["type"] == "text":
            text = seg["text"]
            if i + 1 < len(segments) and segments[i + 1]["type"] == "icon":
                text = text.rstrip("（(").rstrip()
            if not text.strip():
                continue
            if cleaned and cleaned[-1]["type"] == "text":
                cleaned[-1]["text"] += text
            else:
                cleaned.append({"type": "text", "text": text})
        else:
            cleaned.append(seg)
    return cleaned


def _segments_plain_text(segments: list[dict]) -> str:
    parts: list[str] = []
    for seg in segments:
        if seg["type"] == "text":
            parts.append(seg["text"].replace("\n", " "))
        elif seg["type"] == "icon":
            alt = seg.get("alt") or seg.get("image", "")
            if alt:
                parts.append(f"[{alt}]")
    return _clean_text("".join(parts))


def _collect_segment_image_urls(stat: dict) -> dict[str, str]:
    urls: dict[str, str] = {}
    for seg in stat.get("segments", []):
        if seg.get("type") == "icon" and seg.get("image"):
            fn = seg["image"]
            urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    fn = stat.get("value_image")
    if fn:
        urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    return urls


def _parse_bool_icon(td) -> str | None:
    if td.select_one(".t-yes"):
        return "yes"
    if td.select_one(".t-no"):
        return "no"
    return None


def _parse_generic_stat(td, label: str) -> dict:
    bool_icon = _parse_bool_icon(td)
    if bool_icon:
        return {"label": label, "value": "", "extra": "", "bool_icon": bool_icon}

    td_copy = BeautifulSoup(str(td), "lxml").select_one("td") or td
    extra_el = td_copy.select_one(".small-bold, .knockback, .usetime")
    extra = _clean_text(extra_el.get_text(" ", strip=True)) if extra_el else ""
    if extra_el:
        extra_el.extract()

    segments = _parse_rich_segments(td_copy)
    if any(seg.get("type") == "icon" for seg in segments):
        return {
            "label": label,
            "value": _segments_plain_text(segments),
            "extra": extra,
            "segments": segments,
        }

    value = _clean_text(td_copy.get_text())
    if extra:
        extra_core = extra.strip("()（）[] ")
        if extra_core and extra_core in value:
            extra = ""
    stat: dict = {"label": label, "value": value, "extra": extra}
    img_el = td_copy.select_one("img")
    if img_el and img_el.get("src") and not value:
        stat["value_image"] = _filename_from_url(_image_url_from_src(img_el["src"]))
    return stat


BOOL_YES_MARKERS = frozenset({"✔", "✔️", "✅", "☑", "☑️", "✓"})
BOOL_NO_MARKERS = frozenset({"❌", "❎", "✖", "✖️", "☒", "✗", "✘"})


def resolve_bool_icon(stat: dict) -> str | None:
    icon = stat.get("bool_icon")
    if icon in ("yes", "no"):
        return icon
    value = (stat.get("value") or "").strip().replace("\ufe0f", "")
    if value in BOOL_YES_MARKERS:
        return "yes"
    if value in BOOL_NO_MARKERS:
        return "no"
    return None


def _parse_mode_field(td) -> dict[str, str]:
    mode_content = td.select_one(".mode-content")
    if not mode_content:
        return {}
    values: dict[str, str] = {}
    for span in mode_content.select("span[class]"):
        classes = set(span.get("class", []))
        text = _clean_text(span.get_text())
        if not text:
            continue
        if classes & {"m-normal", "m-journey"}:
            values["normal"] = text
        if "m-expert-master" in classes:
            values["expert"] = text
            values["master"] = text
        elif "m-expert" in classes:
            values["expert"] = text
        elif "m-master" in classes:
            values["master"] = text
    return values


def _resolve_mode_values(td) -> dict[str, str]:
    by_mode = _parse_mode_field(td)
    fallback = _clean_text(td.get_text())
    if not by_mode:
        return {"normal": fallback, "expert": fallback, "master": fallback}
    values = {
        "normal": by_mode.get("normal", fallback),
        "expert": by_mode.get("expert", by_mode.get("normal", fallback)),
        "master": by_mode.get("master", by_mode.get("expert", by_mode.get("normal", fallback))),
    }
    return values


def _parse_drop_entity(entity) -> tuple[str, str]:
    name_el = entity.select_one(".entity-name a[title]") or entity.select_one("a[title]")
    if name_el:
        name = _clean_text(name_el.get("title") or name_el.get_text())
    else:
        name = _clean_text(entity.get_text())
    img_el = entity.select_one(".npcimg img") or entity.select_one("img")
    image = ""
    if img_el and img_el.get("src"):
        image = _filename_from_url(_image_url_from_src(img_el["src"]))
    return name, image


def _parse_drop_row(tr) -> dict | None:
    tds = tr.select("td")
    if len(tds) < 3:
        return None
    name, image = _parse_drop_entity(tds[0])
    if not name:
        return None
    qty_by_mode = _resolve_mode_values(tds[1])
    chance_by_mode = _resolve_mode_values(tds[2])
    return {
        "name": name,
        "image": image,
        "qty_by_mode": qty_by_mode,
        "chance_by_mode": chance_by_mode,
    }


def _parse_drop_modes(box) -> list[dict]:
    modes: list[dict] = []
    for tab in box.select(".modetabs .tab"):
        classes = tab.get("class", [])
        mode = "normal"
        if "expert" in classes:
            mode = "expert"
        elif "master" in classes:
            mode = "master"
        label = _clean_text(tab.get_text())
        if label:
            modes.append({"mode": mode, "label": label})
    return modes


_DEFAULT_DROP_MODES = [
    {"mode": "normal", "label": "经典"},
    {"mode": "expert", "label": "专家"},
    {"mode": "master", "label": "大师"},
]

_DROP_MODE_LABEL_EN = {
    "经典": "Classic",
    "专家": "Expert",
    "大师": "Master",
}


def _translate_drop_banner(label: str, locale: str) -> str:
    if locale != "en" or not label:
        return label
    parts = [part.strip() for part in label.split("/")]
    return " / ".join(_DROP_MODE_LABEL_EN.get(part, part) for part in parts if part)


def _translate_mode_label(label: str, locale: str) -> str:
    if locale != "en" or not label:
        return label
    return _DROP_MODE_LABEL_EN.get(label, label)


def parse_drops_from_soup(soup: BeautifulSoup) -> dict | None:
    box = soup.select_one("div.drop.infobox.modesbox")
    if not box:
        return None

    rows: list[dict] = []
    for tr in box.select("table.drop-noncustom tbody tr"):
        if tr.select("th"):
            continue
        row = _parse_drop_row(tr)
        if row:
            rows.append(row)
    if not rows:
        return None

    mode_tabs = _parse_drop_modes(box) or list(_DEFAULT_DROP_MODES)
    modes = []
    for tab in mode_tabs:
        mode_key = tab["mode"]
        entries = []
        for row in rows:
            entries.append(
                {
                    "name": row["name"],
                    "image": row["image"],
                    "quantity": row["qty_by_mode"].get(mode_key, ""),
                    "chance": row["chance_by_mode"].get(mode_key, ""),
                }
            )
        modes.append({**tab, "entries": entries})
    return {"modes": modes}


def _entries_signature(entries: list[dict]) -> tuple:
    return tuple(
        (e.get("name"), e.get("quantity"), e.get("chance"), e.get("image"))
        for e in entries
    )


def compact_drop_modes(drops: dict | None) -> list[dict]:
    if not drops:
        return []
    modes = drops.get("modes", [])
    if len(modes) <= 1:
        return modes
    first_sig = _entries_signature(modes[0].get("entries", []))
    if all(_entries_signature(mode.get("entries", [])) == first_sig for mode in modes[1:]):
        labels = " / ".join(mode.get("label", "") for mode in modes if mode.get("label"))
        return [{"mode": "all", "label": labels, "entries": modes[0].get("entries", [])}]
    return modes


def _join_mode_labels(labels: list[str], locale: str = "zh") -> str:
    if not labels:
        return ""
    if locale == "en":
        labels = [_translate_mode_label(label, locale) for label in labels]
    if len(labels) == 1:
        return labels[0]
    if locale == "en":
        if len(labels) == 2:
            return f"{labels[0]} and {labels[1]}"
        return ", ".join(labels[:-1]) + f" and {labels[-1]}"
    if len(labels) == 2:
        return f"{labels[0]}和{labels[1]}"
    return "、".join(labels[:-1]) + f"和{labels[-1]}"


def _format_mode_values(pairs: list[tuple[str, str]], locale: str = "zh") -> str:
    if not pairs:
        return ""
    value_to_labels: dict[str, list[str]] = {}
    value_order: list[str] = []
    for label, val in pairs:
        val = (val or "").strip()
        if val not in value_to_labels:
            value_to_labels[val] = []
            value_order.append(val)
        if label and label not in value_to_labels[val]:
            value_to_labels[val].append(label)
    if len(value_order) == 1:
        return value_order[0]
    parts = []
    for val in value_order:
        label_text = _join_mode_labels(value_to_labels[val], locale)
        parts.append(f"{label_text}:{val}")
    return "/".join(parts)


def drops_display_block(drops: dict | None, locale: str = "zh") -> dict | None:
    """合并三难度为单行展示，差异写入数量/几率列。"""
    if not drops:
        return None
    modes = drops.get("modes", [])
    if not modes:
        return None

    labels = " / ".join(m.get("label", "") for m in modes if m.get("label"))
    labels = _translate_drop_banner(labels, locale)
    row_count = max((len(m.get("entries", [])) for m in modes), default=0)
    merged: list[dict] = []
    for i in range(row_count):
        per_mode: list[tuple[str, dict]] = []
        for m in modes:
            entries = m.get("entries", [])
            if i < len(entries):
                per_mode.append((m.get("label", ""), entries[i]))
        if not per_mode:
            continue
        base = per_mode[0][1]
        qty_pairs = [(lb, e.get("quantity", "")) for lb, e in per_mode]
        ch_pairs = [(lb, e.get("chance", "")) for lb, e in per_mode]
        merged.append(
            {
                "name": base.get("name", ""),
                "image": base.get("image", ""),
                "quantity": _format_mode_values(qty_pairs, locale),
                "chance": _format_mode_values(ch_pairs, locale),
            }
        )
    return {"label": labels, "entries": merged}


def merge_en_recipe(zh_recipe: dict | None, en_recipe: dict | None) -> dict | None:
    if not en_recipe:
        return en_recipe
    if not zh_recipe:
        return en_recipe
    merged = {
        "station": en_recipe.get("station", ""),
        "ingredients": [],
        "result": dict(en_recipe.get("result") or {}),
    }
    zh_by_image = {
        ing.get("image"): ing for ing in zh_recipe.get("ingredients", []) if ing.get("image")
    }
    for ing in en_recipe.get("ingredients", []):
        entry = dict(ing)
        zh_ing = zh_by_image.get(entry.get("image", ""))
        if zh_ing and zh_ing.get("amount") and not entry.get("amount"):
            entry["amount"] = zh_ing["amount"]
        merged["ingredients"].append(entry)
    zh_res = zh_recipe.get("result") or {}
    res = merged["result"]
    if zh_res.get("amount") and not res.get("amount"):
        res["amount"] = zh_res["amount"]
    merged["result"] = res
    return merged


def _collect_drop_image_urls(drops: dict) -> dict[str, str]:
    urls: dict[str, str] = {}
    for mode in drops.get("modes", []):
        for entry in mode.get("entries", []):
            fn = entry.get("image", "")
            if fn:
                urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    return urls


def _parse_recipe_entry(cell) -> dict:
    span_i = cell.select_one("span.i")
    name, image = "", ""
    if span_i:
        name, image = _parse_item_span(span_i)
    amount_el = cell.select_one("span.am")
    amount = _clean_text(amount_el.get_text()) if amount_el else ""
    entry: dict = {"name": name, "image": image}
    if amount and amount not in ("1", ""):
        entry["amount"] = amount
    return entry


def _parse_item_span(span) -> tuple[str, str]:
    """从 span.i 元素提取 (名称, 图片文件名)"""
    link = span.select_one("a[title]") or span.select_one("a")
    img = span.select_one("img")
    name = ""
    if link:
        name = _clean_text(link.get("title") or link.get_text())
    image = _filename_from_url(_image_url_from_src(img["src"])) if img and img.get("src") else ""
    return name, image


_INTRO_SKIP_DIV_CLASSES = frozenset({
    "message-box",
    "msgbox-color-blue",
    "msgbox-color-red",
    "msgbox-color-green",
    "msgbox-color-orange",
    "nomobile",
    "mw-empty-elt",
    "navbox",
    "toc",
    "hat-note",
    "searchaux",
    "noexcerpt",
    "t-for",
    "t-dablink",
    "t-distinguish",
    "t-about",
    "t-redirect",
    "infobox-wrapper",
    "ajaxHide",
    "reflist",
})


def _iter_wiki_titles(item: dict, key: str) -> list[str]:
    """按优先级返回可用于抓取 Wiki 的标题候选"""
    titles: list[str] = []
    for raw in (item.get("wiki_title"), item.get("name"), key):
        title = _clean_text(raw or "")
        if title and title not in titles:
            titles.append(title)
        match = re.match(r"^(.+?)\(电脑版、主机版、和移动版\)$", title)
        if match:
            base = _clean_text(match.group(1))
            if base and base not in titles:
                titles.append(base)
    en_name = item.get("en_name") or (item.get("en") or {}).get("name")
    if en_name and en_name not in titles:
        titles.append(en_name)
    return titles


def _description_fallback_from_stats(item: dict) -> str | None:
    """无 Wiki 导语时，用 infobox 工具提示或类型信息兜底"""
    for stat in item.get("stats", []):
        if stat.get("label") in ("工具提示", "Tooltip"):
            value = _clean_text(stat.get("value", ""))
            if value:
                return value
    name = _clean_text(item.get("name", ""))
    type_value = ""
    for stat in item.get("stats", []):
        if stat.get("label") in ("类型", "Type"):
            type_value = _clean_text(stat.get("value", ""))
            break
    if name and type_value:
        return f"{name}是一种{type_value}。"
    if name:
        return f"{name}是泰拉瑞亚中的一种物品。"
    return None


    return None


def _apply_description_to_item(item: dict, parsed: dict | str | None) -> bool:
    if isinstance(parsed, dict):
        text = parsed.get("text")
        if not text:
            return False
        item["description"] = text
        rich = parsed.get("rich")
        item["description_rich"] = rich if rich else description_text_to_rich(text)
        return True
    if isinstance(parsed, str) and parsed.strip():
        text = parsed.strip()
        item["description"] = text
        item["description_rich"] = description_text_to_rich(text)
        return True
    return False


def _apply_description_fallback(item: dict) -> bool:
    fallback = _description_fallback_from_stats(item)
    if not fallback:
        return False
    item["description"] = fallback
    item["description_rich"] = description_text_to_rich(fallback)
    return True


async def fetch_item_description(
    session: aiohttp.ClientSession,
    item: dict,
    key: str,
) -> bool:
    """抓取物品导语，依次尝试多个 Wiki 标题"""
    for title in _iter_wiki_titles(item, key):
        html = await fetch_page_html(session, title)
        if not html:
            html = await fetch_page_html(session, title, api_url=API_URL_EN)
        if not html:
            continue
        parsed = parse_description_from_soup(BeautifulSoup(html, "html.parser"))
        if _apply_description_to_item(item, parsed):
            return True
    return _apply_description_fallback(item)


def _is_intro_table(table: Tag) -> bool:
    classes = set(table.get("class") or [])
    if classes & {"info-request", "navbox"}:
        return True
    if "terraria" in classes and classes & {"lined", "mw-collapsible", "sortable"}:
        return True
    if classes == {"terraria"}:
        text = table.get_text(" ", strip=True)
        if any(token in text for token in ("增益", "基础伤害", "跳跃强化", "看 · 论 · 编")):
            return True
    return False


def _should_skip_intro_div(div: Tag) -> str:
    """返回 skip（继续扫描）或 stop（结束导语区）"""
    classes = set(div.get("class") or [])
    if classes & _INTRO_SKIP_DIV_CLASSES:
        return "stop" if "toc" in classes else "skip"
    if "infobox" in classes or "thumb" in classes:
        return "skip"
    text = _clean_text(div.get_text())
    if not text:
        return "skip"
    if text.startswith("目录") or div.get("id") == "toc" or div.select_one("#toc, .toc"):
        return "skip"
    if not classes:
        return "skip"
    return "stop"


def _parse_key_element(el: Tag) -> dict:
    kbd = el.select_one("kbd") or el
    full = _clean_text(kbd.get_text("", strip=True))
    symbol = ""
    sym_span = el.select_one("span[style*='font-size']")
    if sym_span:
        symbol = _clean_text(sym_span.get_text("", strip=True))
    if symbol and full.startswith(symbol):
        label = full[len(symbol) :].strip()
    else:
        label = full
    return {"type": "key", "symbol": symbol, "label": label}


def _parse_description_paragraph_rich(p: Tag) -> list[dict]:
    from bs4 import NavigableString

    segments: list[dict] = []

    def append_text(text: str) -> None:
        text = _clean_text(text)
        if not text:
            return
        if segments and segments[-1]["type"] == "text":
            segments[-1]["text"] += text
        else:
            segments.append({"type": "text", "text": text})

    def walk(node) -> None:
        if isinstance(node, NavigableString):
            append_text(str(node))
            return
        if not isinstance(node, Tag):
            return
        classes = set(node.get("class") or [])
        if node.name == "span" and "key" in classes:
            segments.append(_parse_key_element(node))
            return
        if node.name == "sup" and "reference" in classes:
            ref_num = re.sub(r"[\[\]\s]", "", node.get_text("", strip=True))
            append_text(f"[{ref_num}]" if ref_num else "")
            return
        if node.name == "span" and "coin" in classes:
            amt_el = node.select_one("span.pc, span.gc, span.sc, span.cc")
            if amt_el:
                match = re.search(r"(\d+)", _clean_text(amt_el.get_text()))
                append_text(match.group(1) if match else "")
            return
        if node.name == "img" and node.get("src"):
            segments.append(
                {
                    "type": "icon",
                    "image": _filename_from_url(_image_url_from_src(node["src"])),
                    "alt": _clean_text(node.get("alt", "")),
                }
            )
            return
        if node.name == "i":
            for child in node.children:
                walk(child)
            return
        for child in node.children:
            walk(child)

    for child in p.children:
        walk(child)

    cleaned: list[dict] = []
    for seg in segments:
        if seg["type"] == "text" and not seg["text"].strip():
            continue
        cleaned.append(seg)
    return cleaned


def _rich_segments_to_text(segments: list[dict]) -> str:
    parts: list[str] = []
    for seg in segments:
        if seg["type"] == "text":
            parts.append(seg["text"])
        elif seg["type"] == "key":
            parts.append(f"{seg.get('symbol', '')}{seg.get('label', '')}")
        elif seg["type"] == "icon":
            alt = seg.get("alt") or ""
            if alt:
                parts.append(alt)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


DESCRIPTION_KEY_RE = re.compile(
    r"(⚷)(打开/激活)"
    r"|(⚒)(使用/攻击)"
    r"|(↷)(跳键|跳)"
    r"|(▼)\s*(下)"
    r"|(▲)\s*(上)"
    r"|(◀)\s*(左)"
    r"|(▶)\s*(右)"
)


def _match_description_key(match: re.Match) -> tuple[str, str]:
    groups = [g for g in match.groups() if g is not None]
    if len(groups) >= 2:
        return groups[0], groups[1]
    return "", ""


def description_text_to_rich(text: str) -> list[list[dict]]:
    """从已存储的导语纯文本还原按键分段（兼容旧数据）"""
    if not text:
        return []
    paragraphs: list[list[dict]] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        segments: list[dict] = []
        pos = 0
        for match in DESCRIPTION_KEY_RE.finditer(para):
            if match.start() > pos:
                segments.append({"type": "text", "text": para[pos : match.start()]})
            symbol, label = _match_description_key(match)
            if symbol:
                segments.append({"type": "key", "symbol": symbol, "label": label})
            pos = match.end()
        if pos < len(para):
            segments.append({"type": "text", "text": para[pos:]})
        if not segments:
            segments = [{"type": "text", "text": para}]
        paragraphs.append(segments)
    return paragraphs


def _clean_description_paragraph(p: Tag) -> str:
    """将 Wiki 导语段落转为纯文本"""
    return _rich_segments_to_text(_parse_description_paragraph_rich(p))


def parse_description_from_soup(soup: BeautifulSoup) -> dict | None:
    """提取 Wiki 正文开头导语（infobox 后、目录/章节前的连续段落）"""
    root = (
        soup.select_one("#mw-content-text .mw-parser-output")
        or soup.select_one(".mw-parser-output")
    )
    if not root:
        return None

    stop_tags = frozenset({"h2", "h3", "h4", "ul", "ol", "figure", "style", "script"})
    paragraphs: list[str] = []
    rich_paragraphs: list[list[dict]] = []
    for child in root.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "table":
            if _is_intro_table(child):
                continue
            break
        if child.name == "div":
            action = _should_skip_intro_div(child)
            if action == "skip":
                continue
            break
        if child.name == "blockquote":
            continue
        if child.name == "p":
            rich = _parse_description_paragraph_rich(child)
            text = _rich_segments_to_text(rich)
            if text:
                paragraphs.append(text)
                rich_paragraphs.append(rich)
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


def parse_item_page(html: str, fallback_name: str) -> dict | None:
    """解析物品页面 HTML，无有效 infobox 时返回 None"""
    soup = BeautifulSoup(html, "lxml")
    root = (
        soup.select_one("#mw-content-text .mw-parser-output")
        or soup.select_one(".mw-parser-output")
        or soup
    )
    if root.select_one(".noarticletext"):
        return None

    infobox = soup.select_one("div.infobox.item")
    if not infobox:
        return None

    item: dict = {
        "name": fallback_name,
        "image": "",
        "stats": [],
        "recipe": None,
    }

    title_el = infobox.select_one(".title")
    if title_el:
        item["name"] = _clean_text(title_el.get_text())

    img_el = infobox.select_one(".section.images img")
    if img_el and img_el.get("src"):
        item["image"] = _filename_from_url(_image_url_from_src(img_el["src"]))

    for row in infobox.select(".section.statistics table.stat tr"):
        th, td = row.select_one("th"), row.select_one("td")
        if not th or not td:
            continue
        label = _clean_text(th.get_text())
        if label in RARITY_LABELS:
            item["stats"].append(_parse_rarity_stat(td, label))
            continue
        if label in COIN_STAT_LABELS:
            item["stats"].append(_parse_sell_stat(td, label))
            continue
        if not label:
            continue
        item["stats"].append(_parse_generic_stat(td, label))

    for table in soup.select("table.terraria.cellborder.recipes"):
        # 跳过微光嬗变等带 caption 的配方表
        if table.select_one("caption"):
            continue
        rows = [tr for tr in table.select("tbody tr") if tr.get("data-rowid")]
        if not rows:
            continue
        if fallback_name in OVERVIEW_PAGE_TITLES or item["name"] in OVERVIEW_PAGE_TITLES:
            break
        if len(rows) > MAX_ITEM_RECIPE_ROWS:
            continue
        row = rows[0]
        station_el = row.select_one("td.station")
        station = _clean_text(station_el.get_text()) if station_el else ""
        station = re.sub(r"(?<=[a-zA-Z\)）])or(?=[A-Z])", " or ", station)

        ingredients = []
        for li in row.select("td.ingredients li"):
            span_i = li.select_one("span.i")
            if not span_i:
                continue
            name, image = _parse_item_span(span_i)
            if not name:
                continue
            entry: dict = {"name": name, "image": image}
            amount_el = li.select_one("span.am")
            amount = _clean_text(amount_el.get_text()) if amount_el else ""
            if amount and amount not in ("1", ""):
                entry["amount"] = amount
            ingredients.append(entry)

        result_el = row.select_one("td.result")
        result = _parse_recipe_entry(result_el) if result_el else {"name": "", "image": ""}
        if not result.get("name"):
            result["name"] = item["name"]
        if not result.get("image"):
            result["image"] = item["image"]

        item["recipe"] = {
            "station": station,
            "ingredients": ingredients,
            "result": result,
        }
        break

    drops = parse_drops_from_soup(soup)
    if drops:
        item["drops"] = drops

    parsed = parse_description_from_soup(soup)
    if not _apply_description_to_item(item, parsed):
        _apply_description_fallback(item)

    _apply_overview_page_meta(item, fallback_name)

    return item


def _extract_en_locale(en_item: dict, zh_item: dict) -> dict:
    return {
        "name": en_item.get("name", ""),
        "image": en_item.get("image") or zh_item.get("image", ""),
        "stats": en_item.get("stats", []),
        "recipe": en_item.get("recipe"),
        "drops": en_item.get("drops"),
        "description": en_item.get("description"),
        "description_rich": en_item.get("description_rich"),
    }


async def fetch_en_langlink(session: aiohttp.ClientSession, zh_title: str) -> str | None:
    params = {
        "action": "query",
        "titles": zh_title,
        "prop": "langlinks",
        "lllang": "en",
        "format": "json",
        "redirects": "1",
    }
    try:
        async with session.get(
            API_URL, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if page.get("missing"):
            continue
        for link in page.get("langlinks", []):
            if link.get("lang") == "en":
                title = _clean_text(link.get("*", ""))
                if title:
                    return title
    return None


async def fetch_en_page_html(session: aiohttp.ClientSession, en_title: str) -> str | None:
    return await fetch_page_html(session, en_title, api_url=API_URL_EN)


def _guess_en_title_from_image(item: dict) -> str | None:
    image = item.get("image", "")
    if not image:
        return None
    stem = os.path.splitext(image)[0]
    if not stem:
        return None
    return stem.replace("_", " ")


async def attach_en_locale(
    session: aiohttp.ClientSession,
    item: dict,
    zh_title: str,
    force: bool = False,
) -> bool:
    if not force and item.get("en", {}).get("name"):
        return False

    en_title = await fetch_en_langlink(session, zh_title)
    if not en_title:
        en_title = _guess_en_title_from_image(item)

    if not en_title:
        return False

    en_html = await fetch_en_page_html(session, en_title)
    if not en_html:
        return False

    en_item = parse_item_page(en_html, en_title)
    if not en_item or not en_item.get("name"):
        return False

    item["en"] = _extract_en_locale(en_item, item)
    item["en_name"] = en_item["name"]
    return True


async def backfill_en_locales(
    session: aiohttp.ClientSession,
    items: dict[str, dict],
    force: bool = False,
    limit: int | None = None,
) -> int:
    pending = [
        key
        for key, item in items.items()
        if force or not item.get("en", {}).get("name")
    ]
    pending.sort()
    if limit:
        pending = pending[:limit]

    updated = 0
    semaphore = asyncio.Semaphore(8)

    async def _process_one(key: str) -> bool:
        async with semaphore:
            item = items[key]
            zh_title = item.get("wiki_title") or item.get("name") or key
            ok = await attach_en_locale(session, item, zh_title, force=force)
            await asyncio.sleep(0.05)
            return ok

    batch_size = 50
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        results = await asyncio.gather(*[_process_one(key) for key in batch])
        updated += sum(1 for ok in results if ok)
        done = batch_start + len(batch)
        logger.info(f"英文数据回填进度 {done}/{len(pending)}，新增 {updated}")
        with open(ITEMS_JSON, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    return updated


async def backfill_drops(
    session: aiohttp.ClientSession,
    items: dict[str, dict],
    force: bool = False,
    limit: int | None = None,
) -> tuple[int, dict[str, str]]:
    pending = [
        key
        for key, item in items.items()
        if force or "drops" not in item
    ]
    pending.sort()
    if limit:
        pending = pending[:limit]

    image_urls: dict[str, str] = {}
    found = 0
    semaphore = asyncio.Semaphore(8)

    async def _process_one(key: str) -> bool:
        async with semaphore:
            item = items[key]
            title = item.get("wiki_title") or item.get("name") or key
            html = await fetch_page_html(session, title)
            if not html:
                item["drops"] = None
                await asyncio.sleep(0.05)
                return False
            soup = BeautifulSoup(html, "html.parser")
            drops = parse_drops_from_soup(soup)
            item["drops"] = drops
            if drops:
                image_urls.update(_collect_drop_image_urls(drops))
            await asyncio.sleep(0.05)
            return drops is not None

    batch_size = 50
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        results = await asyncio.gather(*[_process_one(key) for key in batch])
        found += sum(1 for ok in results if ok)
        done = batch_start + len(batch)
        logger.info(f"掉落来源回填进度 {done}/{len(pending)}，发现 {found} 个")
        with open(ITEMS_JSON, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    return found, image_urls


async def backfill_descriptions(
    session: aiohttp.ClientSession,
    items: dict[str, dict],
    force: bool = False,
    limit: int | None = None,
) -> int:
    pending = [
        key
        for key, item in items.items()
        if force or not item.get("description")
    ]
    pending.sort()
    if limit:
        pending = pending[:limit]

    found = 0
    semaphore = asyncio.Semaphore(4)

    async def _process_one(key: str) -> bool:
        async with semaphore:
            item = items[key]
            description = None
            for attempt in range(3):
                if await fetch_item_description(session, item, key):
                    description = item.get("description")
                    break
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
            await asyncio.sleep(0.12)
            return bool(description)

    batch_size = 50
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        results = await asyncio.gather(*[_process_one(key) for key in batch])
        found += sum(1 for ok in results if ok)
        done = batch_start + len(batch)
        logger.info(f"描述回填进度 {done}/{len(pending)}，发现 {found} 个")
        with open(ITEMS_JSON, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    return found


async def fetch_category_members(
    session: aiohttp.ClientSession, category: str
) -> list[str]:
    """获取分类下所有页面标题（处理分页）"""
    titles: list[str] = []
    params: dict = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmlimit": "500",
        "format": "json",
    }
    while True:
        async with session.get(API_URL, params=params, headers=HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()
        members = data.get("query", {}).get("categorymembers", [])
        for m in members:
            if m.get("ns") == 0:
                titles.append(m["title"])
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
        params["cmcontinue"] = cont
        await asyncio.sleep(0.2)
    return titles


async def fetch_page_html(
    session: aiohttp.ClientSession, title: str, api_url: str = API_URL
) -> str | None:
    """通过 MediaWiki API 获取页面 HTML（比直接抓取更稳定）"""
    params = {
        "action": "parse",
        "page": title,
        "format": "json",
        "prop": "text",
        "redirects": "1",
    }
    for attempt in range(3):
        try:
            async with session.get(
                api_url, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=25)
            ) as resp:
                if resp.status == 404:
                    return None
                data = await resp.json()
                if "error" in data:
                    code = data["error"].get("code", "")
                    if code in ("missingtitle", "invalidtitle"):
                        return None
                    if attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    return None
                html = data.get("parse", {}).get("text", {}).get("*", "")
                if html and "Just a second" not in html:
                    return html
                if attempt < 2:
                    await asyncio.sleep(2.0 * (attempt + 1))
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
    return None


async def download_image(
    session: aiohttp.ClientSession,
    filename: str,
    url: str,
    semaphore: asyncio.Semaphore,
) -> bool:
    if not filename or not url:
        return False
    local_path = os.path.join(IMAGES_DIR, filename)
    if os.path.exists(local_path):
        return True

    async with semaphore:
        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 404:
                    return False
                content = await resp.read()
                if resp.status >= 500 or not content:
                    return False
            with open(local_path, "wb") as f:
                f.write(content)
            return True
        except Exception:
            return False


def _collect_image_urls(item: dict) -> dict[str, str]:
    """收集物品涉及的所有图片 filename -> url"""
    urls: dict[str, str] = {}
    if item.get("image"):
        fn = item["image"]
        urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    for stat in item.get("stats", []):
        urls.update(_collect_segment_image_urls(stat))
    recipe = item.get("recipe")
    if recipe:
        for ing in recipe.get("ingredients", []):
            fn = ing.get("image", "")
            if fn:
                urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
        res = recipe.get("result", {})
        fn = res.get("image", "")
        if fn:
            urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    drops = item.get("drops")
    if drops:
        urls.update(_collect_drop_image_urls(drops))
    return urls


def _apply_overview_page_meta(item: dict, wiki_title: str) -> None:
    meta = OVERVIEW_PAGES.get(wiki_title) or OVERVIEW_PAGES.get(item.get("name", ""))
    if not meta:
        return
    item["page_type"] = "overview"
    item["recipe"] = None
    aliases = list(meta.get("aliases") or [])
    item["aliases"] = aliases
    if wiki_title:
        item["wiki_title"] = wiki_title


def _wing_search_terms(name: str) -> list[str]:
    terms: list[str] = []
    if name.endswith("之翼") and len(name) > 2:
        terms.append(name[:-2])
    return terms


def _parse_wing_row(tr: Tag) -> dict | None:
    anchor = tr.select_one("s.anchor[id]")
    if not anchor:
        return None

    display_el = (
        tr.select_one("td.il2c span[title]")
        or tr.select_one("td.il1c img[title]")
        or tr.select_one("td.il2c img[title]")
    )
    name = _clean_text(display_el.get("title", "")) if display_el else ""
    if not name:
        name = _clean_text(anchor.get("id", "")).replace("_", " ")
    if not name:
        return None

    img_el = tr.select_one("td.il1c img") or tr.select_one("span.i img")
    image = _filename_from_url(_image_url_from_src(img_el["src"])) if img_el and img_el.get("src") else ""

    cells = tr.select("td")
    source = _clean_text(cells[3].get_text(" ", strip=True)) if len(cells) > 3 else ""
    flight_time = _clean_text(cells[4].get_text(" ", strip=True)) if len(cells) > 4 else ""
    height = _clean_text(cells[5].get_text(" ", strip=True)) if len(cells) > 5 else ""
    speed_attr = _clean_text(cells[6].get_text(" ", strip=True)) if len(cells) > 6 else ""
    h_speed = _clean_text(cells[8].get_text(" ", strip=True)) if len(cells) > 8 else ""
    h_accel = _clean_text(cells[9].get_text(" ", strip=True)) if len(cells) > 9 else ""
    v_mult = _clean_text(cells[10].get_text(" ", strip=True)) if len(cells) > 10 else ""

    rarity_cell = tr.select_one("td .rarity")
    if rarity_cell:
        rarity_cell = rarity_cell.find_parent("td")
    rarity_stat = _parse_rarity_stat(rarity_cell, "稀有度") if rarity_cell else None

    notes: list[str] = []
    for li in tr.select("td ul li"):
        note = _clean_text(li.get_text(" ", strip=True))
        if note:
            notes.append(note)

    stats: list[dict] = [{"label": "类型", "value": "配饰", "extra": ""}]
    if source:
        stats.append({"label": "来源", "value": source, "extra": ""})
    if flight_time:
        stats.append({"label": "飞行时间", "value": flight_time, "extra": ""})
    if height:
        stats.append({"label": "高度（格）", "value": height, "extra": ""})
    if speed_attr:
        stats.append({"label": "速度", "value": speed_attr, "extra": ""})
    if h_speed:
        stats.append({"label": "最大水平速度", "value": h_speed, "extra": ""})
    if h_accel:
        stats.append({"label": "水平加速度", "value": h_accel, "extra": ""})
    if v_mult:
        stats.append({"label": "垂直倍率", "value": v_mult, "extra": ""})
    if rarity_stat:
        stats.append(rarity_stat)

    desc_parts: list[str] = []
    if source:
        desc_parts.append(source)
    desc_parts.extend(notes)

    item: dict = {
        "name": name,
        "image": image,
        "stats": stats,
        "recipe": None,
        "page_type": "wing",
        "from_wings_table": True,
        "parent_page": "翅膀",
    }
    if desc_parts:
        item["description"] = "\n\n".join(desc_parts)
        item["description_rich"] = description_text_to_rich(item["description"])
    terms = _wing_search_terms(name)
    if terms:
        item["search_terms"] = terms
    return item


def parse_wings_from_soup(soup: BeautifulSoup) -> dict[str, dict]:
    """从「翅膀」总览页的对比表中解析各翅膀条目"""
    wings: dict[str, dict] = {}
    for table in soup.select("table.terraria"):
        anchors = table.select("s.anchor[id]")
        if len(anchors) < 10:
            continue
        for tr in table.select("tbody tr"):
            wing = _parse_wing_row(tr)
            if wing:
                wings[wing["name"]] = wing
        if wings:
            break
    return wings


def _merge_wing_en_names(zh_wings: dict[str, dict], en_wings: dict[str, dict]) -> None:
    en_by_image = {w.get("image"): w for w in en_wings.values() if w.get("image")}
    for wing in zh_wings.values():
        en_wing = en_by_image.get(wing.get("image"))
        if not en_wing:
            continue
        wing["en_name"] = en_wing["name"]
        wing["en"] = {
            "name": en_wing["name"],
            "image": en_wing.get("image") or wing.get("image", ""),
            "stats": en_wing.get("stats", []),
            "recipe": None,
            "description": en_wing.get("description"),
            "description_rich": en_wing.get("description_rich"),
        }


def _merge_parsed_wings(items: dict[str, dict], wings: dict[str, dict]) -> int:
    added = 0
    for name, wing in wings.items():
        existing = items.get(name)
        if existing and not existing.get("from_wings_table"):
            continue
        items[name] = wing
        added += 1
    return added


def _existing_wiki_titles(items: dict[str, dict]) -> set[str]:
    titles = set(items.keys())
    for item in items.values():
        wt = item.get("wiki_title")
        if wt:
            titles.add(wt)
        for alias in item.get("aliases") or []:
            titles.add(alias)
    return titles


async def refresh_overview_pages(
    session: aiohttp.ClientSession,
    items: dict[str, dict],
) -> int:
    """刷新 Wiki 物品类型总览页（如「翅膀」）并展开翅膀子条目"""
    updated = 0
    image_urls: dict[str, str] = {}

    for title, meta in OVERVIEW_PAGES.items():
        html = await fetch_page_html(session, title)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        item = parse_item_page(html, title)
        if not item:
            continue
        name = item["name"]
        item["wiki_title"] = title
        _apply_overview_page_meta(item, title)
        await attach_en_locale(session, item, title)
        if meta.get("aliases"):
            item["aliases"] = list(meta["aliases"])
        items[name] = item
        updated += 1

        zh_wings = parse_wings_from_soup(soup)
        en_html = await fetch_page_html(session, "Wings", api_url=API_URL_EN)
        if en_html:
            en_wings = parse_wings_from_soup(BeautifulSoup(en_html, "html.parser"))
            _merge_wing_en_names(zh_wings, en_wings)
        wing_added = _merge_parsed_wings(items, zh_wings)
        updated += wing_added
        for wing in zh_wings.values():
            image_urls.update(_collect_image_urls(wing))

        await asyncio.sleep(0.15)

    if updated:
        if image_urls:
            semaphore = asyncio.Semaphore(8)
            await asyncio.gather(
                *[
                    download_image(session, fn, url, semaphore)
                    for fn, url in image_urls.items()
                ]
            )
        with open(ITEMS_JSON, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    return updated


def _load_existing_items() -> dict[str, dict]:
    if not os.path.exists(ITEMS_JSON):
        return {}
    try:
        with open(ITEMS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


async def update_wiki_data(
    limit: int | None = None,
    force: bool = False,
    en_limit: int | None = None,
    en_only: bool = False,
    desc_only: bool = False,
    desc_limit: int | None = None,
) -> dict:
    """增量（或全量）更新 Wiki 离线数据，返回统计信息。"""
    os.makedirs(IMAGES_DIR, exist_ok=True)

    items: dict[str, dict] = {} if force else _load_existing_items()
    before_total = len(items)
    pages_scanned = 0
    new_count = 0
    images_total = 0
    images_ok = 0
    en_backfill_count = 0
    drops_backfill_count = 0
    desc_backfill_count = 0
    overview_count = 0

    connector = aiohttp.TCPConnector(limit=10)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        if desc_only:
            desc_backfill_count = await backfill_descriptions(
                session,
                items,
                force=force,
                limit=desc_limit,
            )
        elif not en_only:
            all_titles: set[str] = set(OVERVIEW_PAGE_TITLES)
            for cat in CATEGORIES:
                titles = await fetch_category_members(session, cat)
                all_titles.update(titles)
                await asyncio.sleep(0.3)

            existing_titles = _existing_wiki_titles(items)
            title_list = sorted(all_titles)
            if not force:
                title_list = [t for t in title_list if t not in existing_titles]
            if limit:
                title_list = title_list[:limit]
            pages_scanned = len(title_list)

            image_urls: dict[str, str] = {}

            for i, title in enumerate(title_list, 1):
                html = await fetch_page_html(session, title)
                if not html:
                    continue
                item = parse_item_page(html, title)
                if not item:
                    continue
                name = item["name"]
                if not force and name in items:
                    continue
                item["wiki_title"] = title
                _apply_overview_page_meta(item, title)
                items[name] = item
                await attach_en_locale(session, item, title)
                image_urls.update(_collect_image_urls(item))
                new_count += 1
                if i % 50 == 0 or i == len(title_list):
                    logger.info(
                        f"Wiki 更新进度 {i}/{len(title_list)}，新增 {new_count}，总计 {len(items)}"
                    )
                await asyncio.sleep(0.15)

            images_total = len(image_urls)
            if image_urls:
                semaphore = asyncio.Semaphore(8)
                tasks = [
                    download_image(session, fn, url, semaphore)
                    for fn, url in image_urls.items()
                ]
                results = await asyncio.gather(*tasks)
                images_ok = sum(1 for r in results if r)

            overview_count = await refresh_overview_pages(session, items)

        effective_en_limit = en_limit if en_only else (100 if en_limit is None else en_limit)
        if not desc_only:
            en_backfill_count = await backfill_en_locales(
                session,
                items,
                force=bool(en_only and force),
                limit=effective_en_limit,
            )

        if not en_only and not desc_only:
            drops_limit = 50 if limit is None else min(50, limit or 50)
            drops_backfill_count, drop_image_urls = await backfill_drops(
                session, items, limit=drops_limit
            )
            if drop_image_urls:
                drop_total = len(drop_image_urls)
                semaphore = asyncio.Semaphore(8)
                tasks = [
                    download_image(session, fn, url, semaphore)
                    for fn, url in drop_image_urls.items()
                ]
                drop_ok = sum(1 for r in await asyncio.gather(*tasks) if r)
                images_total += drop_total
                images_ok += drop_ok

            desc_backfill_count = await backfill_descriptions(
                session,
                items,
                limit=desc_limit if desc_limit is not None else 100,
            )

    with open(ITEMS_JSON, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    return {
        "ok": True,
        "force": force,
        "before_total": before_total,
        "new_count": new_count,
        "total": len(items),
        "pages_scanned": pages_scanned,
        "images_total": images_total,
        "images_ok": images_ok,
        "en_backfill_count": en_backfill_count,
        "drops_backfill_count": drops_backfill_count,
        "desc_backfill_count": desc_backfill_count,
        "overview_count": overview_count,
    }


async def main(
    limit: int | None = None,
    force: bool = False,
    en_only: bool = False,
    en_limit: int | None = None,
    desc_only: bool = False,
    desc_limit: int | None = None,
) -> None:
    if desc_only:
        existing = _load_existing_items()
        print(f"描述回填模式：已有 {len(existing)} 个物品", flush=True)
    elif en_only:
        existing = _load_existing_items()
        print(f"英文回填模式：已有 {len(existing)} 个物品", flush=True)
    elif force:
        print("全量模式：将重新抓取所有物品", flush=True)
    else:
        existing = _load_existing_items()
        print(f"增量模式：已有 {len(existing)} 个物品，跳过已存在的", flush=True)

    print("正在收集并更新 Wiki 数据...", flush=True)
    result = await update_wiki_data(
        limit=limit,
        force=force,
        en_only=en_only,
        en_limit=en_limit,
        desc_only=desc_only,
        desc_limit=desc_limit,
    )
    print(
        f"更新完成：新增 {result['new_count']} 个，"
        f"共 {result['total']} 个物品，"
        f"新图片 {result['images_ok']}/{result['images_total']}，"
        f"英文回填 {result['en_backfill_count']} 个，"
        f"掉落来源 {result['drops_backfill_count']} 个，"
        f"描述 {result['desc_backfill_count']} 个",
        flush=True,
    )
    print(f"已保存到 {ITEMS_JSON}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="准备泰拉瑞亚 Wiki 离线数据")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 个页面（调试用）")
    parser.add_argument("--force", action="store_true", help="全量重建，覆盖已有数据")
    parser.add_argument(
        "--en-only",
        action="store_true",
        help="仅回填英文数据，不抓取中文 Wiki",
    )
    parser.add_argument(
        "--en-limit",
        type=int,
        default=None,
        help="英文回填最多处理 N 个物品（调试用）",
    )
    parser.add_argument(
        "--desc-only",
        action="store_true",
        help="仅回填 Wiki 导语描述，不抓取其他数据",
    )
    parser.add_argument(
        "--desc-limit",
        type=int,
        default=None,
        help="描述回填最多处理 N 个物品（调试用）",
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            main(
                limit=args.limit,
                force=args.force,
                en_only=args.en_only,
                en_limit=args.en_limit,
                desc_only=args.desc_only,
                desc_limit=args.desc_limit,
            )
        )
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(1)
