"""
泰拉瑞亚 Wiki 离线数据准备脚本
================================
一次性运行，从 terraria.wiki.gg 中文 Wiki 爬取物品数据并保存到本地。

用法:
    python prepare_data.py              # 增量更新，仅抓取新增物品
    python prepare_data.py --limit 20   # 调试：仅处理前 20 个新页面
    python prepare_data.py --force      # 全量重建（覆盖已有数据）

本地 Wiki 镜像（可选，显著加速开发）:
    先运行 ../terraria_data/mirror_wiki.py 抓取页面
    prepare_data 会优先读 terraria_data/wiki/ 下的 HTML
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
CATEGORIES_DIR = os.path.join(DATA_DIR, "categories")
MOUNTS_JSON = os.path.join(CATEGORIES_DIR, "mounts.json")
MOUNT_CATEGORY = "Category:坐骑召唤物品"
MOUNT_OVERVIEW_PAGE = "坐骑"
# Wiki 合并页/非物品栏条目，不应出现在 mounts.json
MOUNT_CATALOG_EXCLUDE = frozenset(
    {"马鞍", "轮滑鞋", "矿车", "矿车轨道", "挖掘鼹鼠矿车"}
)

PETS_JSON = os.path.join(CATEGORIES_DIR, "pets.json")
PET_OVERVIEW_PAGE = "宠物"
PET_TABLE_IDS = (
    "table-Pets",
    "table-Master-Mode-Pets",
    "table-Light-Pets",
    "table-Master-Mode-Light-Pets",
)
PET_CATEGORY = "Category:宠物召唤物品"
# 联动页等未列入「宠物」总览表的宠物召唤物
PET_SUPPLEMENT_OVERVIEW_PAGES = ("联动物品",)

_WIKI_MIRROR_DIR = os.path.normpath(
    os.environ.get(
        "TERRARIA_WIKI_MIRROR",
        os.path.join(os.path.dirname(_PLUGIN_DIR), "terraria_data"),
    )
)
if os.path.isdir(_WIKI_MIRROR_DIR) and _WIKI_MIRROR_DIR not in sys.path:
    sys.path.insert(0, _WIKI_MIRROR_DIR)

try:
    from wiki_cache import api_url_to_locale, load_page_html as _load_mirror_html
except ImportError:
    def _load_mirror_html(title: str, locale: str = "zh") -> str | None:  # type: ignore[misc]
        return None

    def api_url_to_locale(api_url: str) -> str:  # type: ignore[misc]
        return "en" if api_url == API_URL_EN else "zh"

CATEGORIES = [
    "Category:工具物品",
    "Category:武器物品",
    "Category:弹药物品",
    "Category:盔甲物品",
    "Category:盔甲套装",
    "Category:家具物品",
    "Category:制作站物品",
    "Category:钱币",
    "Category:矿石物品",
    "Category:锭物品",
    "Category:配饰物品",
    "Category:物块物品",
    "Category:墙物品",
    "Category:油漆",
    "Category:宝石物品",
    "Category:时装物品",
    "Category:染料物品",
    "Category:药水物品",
    "Category:机械物品",
    "Category:仆从召唤物品",
    "Category:其他物品",
    "Category:近战武器",
    "Category:远程武器",
    "Category:魔法武器",
    "Category:召唤武器",
    "Category:制作材料物品",
]

# Wiki 上的物品类型总览页（非单个物品，有独立 infobox + 导语）
OVERVIEW_PAGES: dict[str, dict] = {
    "翅膀": {},
}

OVERVIEW_PAGE_TITLES = frozenset(OVERVIEW_PAGES)

# 常用别名（Wiki 正式名 → 玩家俗称）
ITEM_SEARCH_ALIASES: dict[str, list[str]] = {
    "炼金瓶": ["炼药瓶"],
    "放置的瓶子": ["炼药瓶", "放置瓶子"],
}
HEADERS = {
    "User-Agent": "AstrBot-TerrariaQuery/1.0 (offline data preparation; +https://docs.astrbot.app)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

logger = logging.getLogger(__name__)


def _category_data_module():
    try:
        from . import category_data
    except ImportError:
        import category_data
    return category_data


def _biome_data_module():
    try:
        from . import biome_data
    except ImportError:
        import biome_data
    return biome_data


def _npc_data_module():
    try:
        from . import npc_data
    except ImportError:
        import npc_data
    return npc_data


def _persist_items(items: dict[str, dict]) -> None:
    cd = _category_data_module()
    strip_english_fields(items)
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    cd.persist_items_to_categories(items, categories_dir=CATEGORIES_DIR)


async def _persist_items_async(
    session: aiohttp.ClientSession, items: dict[str, dict]
) -> None:
    cd = _category_data_module()
    strip_english_fields(items)
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    title_to_keys = await cd.build_title_category_map(session)
    cd.persist_items_to_categories(
        items,
        categories_dir=CATEGORIES_DIR,
        title_to_keys=title_to_keys,
        mounts=_load_existing_mounts(),
        pets=_load_existing_pets(),
    )


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
    name = name or "unknown.png"
    return _normalize_image_filename(name)


def _normalize_image_filename(filename: str) -> str:
    """Wiki 缩略图名 17px-Item.png → Item.png"""
    if not filename:
        return ""
    return re.sub(r"^\d+px-", "", filename, flags=re.I) or filename


RARITY_LABELS = frozenset({"稀有度", "Rarity"})
SELL_LABELS = frozenset({"卖出", "Sell"})
BUY_LABELS = frozenset({"买入", "Buy"})
COIN_STAT_LABELS = SELL_LABELS | BUY_LABELS

# Wiki 稀有度：sortkey「NN*」= 稀有度级别 N；显示名称/颜色以页面链接 title 为准
#（如 08* → Yellow/黄色）。翅膀对比表与普通物品 infobox 使用同一套规则。
RARITY_SORTKEY_EN = {
    "00*": "White",
    "01*": "Blue",
    "02*": "Blue",
    "03*": "Green",
    "04*": "Light Red",
    "05*": "Pink",
    "06*": "Pink",
    "07*": "Lime",
    "08*": "Yellow",
    "09*": "Cyan",
    "10*": "Red",
    "11*": "Yellow",
    "12*": "Rainbow",
}

RARITY_EN_TO_ZH = {
    "Gray": "灰色",
    "White": "白色",
    "Blue": "蓝色",
    "Green": "绿色",
    "Orange": "橙色",
    "Light Red": "浅红色",
    "Pink": "粉红色",
    "Light Purple": "浅紫色",
    "Purple": "紫色",
    "Lime": "淡紫色",
    "Violet": "淡紫色",
    "Red": "红色",
    "Yellow": "黄色",
    "Cyan": "渐变色",
    "Rainbow": "彩虹",
    "Gradient": "渐变色",
}

RARITY_EN_TO_HEX = {
    "Gray": "#b8b8b8",
    "White": "#ffffff",
    "Blue": "#5a9cff",
    "Green": "#55dd55",
    "Orange": "#ffaa44",
    "Light Red": "#ff7777",
    "Pink": "#ff88bb",
    "Light Purple": "#cc88ff",
    "Purple": "#aa55ff",
    "Lime": "#dd66ff",
    "Violet": "#dd66ff",
    "Red": "#ff5050",
    "Yellow": "#ffff66",
    "Cyan": "#88ff88",
    "Rainbow": "#88ff88",
    "Gradient": "#88ff88",
}

RARITY_SORTKEY_ZH = {
    sk: RARITY_EN_TO_ZH.get(en, en) for sk, en in RARITY_SORTKEY_EN.items()
}

RARITY_SORTKEY_HEX = {
    sk: RARITY_EN_TO_HEX.get(en, "#ffffff") for sk, en in RARITY_SORTKEY_EN.items()
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
    """解析稀有度。翅膀对比表与物品 infobox 结构相同，均优先采用链接 title。"""
    sortkey = _extract_rarity_sortkey(td)
    link = td.select_one(".rarity a") or td.select_one("a")
    title = link.get("title", "") if link else ""
    en_name = _extract_rarity_name_from_title(title)
    if label == "稀有度":
        display = RARITY_EN_TO_ZH.get(en_name) or RARITY_SORTKEY_ZH.get(
            sortkey, en_name or sortkey
        )
    else:
        display = en_name or RARITY_SORTKEY_EN.get(sortkey, sortkey)
    color = RARITY_EN_TO_HEX.get(en_name) or RARITY_SORTKEY_HEX.get(
        sortkey, "#ffffff"
    )
    return {
        "label": label,
        "value": display or _clean_text(td.get_text()),
        "extra": "",
        "sortkey": sortkey,
        "color": color,
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
            if segments and segments[-1]["type"] == "text":
                segments[-1]["text"] += "\n"
            else:
                segments.append({"type": "text", "text": "\n"})
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
            parts.append(seg["text"])
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
    if segments:
        has_icons = any(seg.get("type") == "icon" for seg in segments)
        has_multiline = any(
            seg.get("type") == "text" and "\n" in seg.get("text", "")
            for seg in segments
        )
        if has_icons or has_multiline:
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
    if not name:
        sort_val = cell.get("data-sort-value")
        if sort_val:
            name = _clean_text(sort_val)
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
    elif img and img.get("title"):
        name = _clean_text(img.get("title"))
    if not name:
        title_el = span.select_one("[title]")
        if title_el:
            name = _clean_text(title_el.get("title") or title_el.get_text())
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


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _iter_zh_wiki_titles(item: dict, key: str) -> list[str]:
    """中文描述抓取用的 Wiki 标题（不含英文 en_name）。"""
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
    return titles


def _iter_wiki_titles(item: dict, key: str) -> list[str]:
    """按优先级返回可用于抓取 Wiki 的标题候选（仅中文）。"""
    return _iter_zh_wiki_titles(item, key)


def _item_tooltip(item: dict) -> str:
    for stat in item.get("stats", []):
        if stat.get("label") in ("工具提示", "Tooltip"):
            return _clean_text(stat.get("value", ""))
    return ""


def _description_is_tooltip_only(item: dict) -> bool:
    """描述与 infobox 工具提示完全相同（多为 Wiki 抓取失败后的兜底）。"""
    desc = _clean_text(item.get("description") or "")
    tooltip = _item_tooltip(item)
    return bool(desc and tooltip and desc == tooltip)


def _description_fallback_from_stats(item: dict, *, skip_tooltip: bool = False) -> str | None:
    """无 Wiki 导语时，用 infobox 工具提示或类型信息兜底"""
    if not skip_tooltip:
        tooltip = _item_tooltip(item)
        if tooltip:
            return tooltip
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


def _apply_description_fallback(item: dict, *, skip_tooltip: bool = False) -> bool:
    fallback = _description_fallback_from_stats(item, skip_tooltip=skip_tooltip)
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
    """抓取物品中文导语（仅中文 Wiki，不回退英文 API）。"""
    _relocate_misplaced_en_description(item)
    for title in _iter_zh_wiki_titles(item, key):
        html = await fetch_page_html(session, title, api_url=API_URL)
        if not html:
            continue
        parsed = parse_description_from_soup(BeautifulSoup(html, "html.parser"))
        if _apply_description_to_item(item, parsed):
            return True
    # 回填场景跳过工具提示兜底，避免把短 tooltip 当成 Wiki 导语
    return _apply_description_fallback(item, skip_tooltip=True)


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


def _is_skippable_intro_table(table: Tag) -> bool:
    """导语区内嵌的小表格（如减益持续时间），不应截断后续列表。"""
    if _is_intro_table(table):
        return True
    classes = set(table.get("class") or [])
    if "float-right" in classes or "floatleft" in classes:
        return True
    if "terraria" in classes and table.select_one("caption"):
        if not table.select_one("td.ingredients, td.result, td.station"):
            return True
    return False


def _append_description_list(
    paragraphs: list[str],
    rich_paragraphs: list[list[dict]],
    text: str,
    rich: list[dict],
) -> None:
    """追加列表块；若上一段已是列表则合并（应对列表被表格打断的情况）。"""
    if paragraphs and paragraphs[-1].startswith("· "):
        paragraphs[-1] = f"{paragraphs[-1]}\n{text}"
        rich_paragraphs[-1].extend([{"type": "text", "text": "\n"}, *rich])
        return
    paragraphs.append(text)
    rich_paragraphs.append(rich)


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
    if not symbol and label:
        match = _KEY_SYMBOL_SPLIT_RE.match(label)
        if match:
            symbol, label = match.group(1), match.group(2)
    return {"type": "key", "symbol": symbol, "label": label}


def _parse_coin_span(node: Tag) -> dict | None:
    """解析 Wiki span.coin 为描述/属性中的金币分段。"""
    for cls in COIN_SPECS:
        amt_el = node.select_one(f"span.{cls}")
        if not amt_el:
            continue
        match = re.search(r"(\d+)", _clean_text(amt_el.get_text()))
        if match:
            return {
                "type": "coin",
                "amount": match.group(1),
                "coin_type": cls,
            }
    return None


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
            coin = _parse_coin_span(node)
            if coin:
                segments.append(coin)
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
        elif seg["type"] == "coin":
            abbr = {"pc": "PC", "gc": "GC", "sc": "SC", "cc": "CC"}.get(
                seg.get("coin_type", ""), ""
            )
            parts.append(f"{seg.get('amount', '')} {abbr}".strip())
        elif seg["type"] == "icon":
            alt = seg.get("alt") or ""
            if alt:
                parts.append(alt)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


DESCRIPTION_KEY_RE = re.compile(
    r"(⚷)(打开/激活)"
    r"|(⚒)(使用/攻击)"
    r"|(↷)\s*(跳键|跳)"
    r"|(▼)\s*(下)"
    r"|(▲)\s*(上)"
    r"|(◀)\s*(左)"
    r"|(▶)\s*(右)"
)

_KEY_SYMBOL_SPLIT_RE = re.compile(r"^([▼▲◀▶↷⚷⚒])(?:\s*)(.*)$")


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


def _parse_description_list_block(list_el: Tag) -> tuple[str, list[dict]] | None:
    """将导语区的 ul/ol 转为描述文本与富文本段落（如配饰效果列表）。"""
    lines: list[str] = []
    combined_rich: list[dict] = []
    ordered = list_el.name == "ol"
    for idx, li in enumerate(list_el.find_all("li", recursive=False), start=1):
        rich = _parse_description_paragraph_rich(li)
        text = _rich_segments_to_text(rich)
        if not text:
            continue
        prefix = f"{idx}. " if ordered else "· "
        lines.append(f"{prefix}{text}")
        if combined_rich:
            combined_rich.append({"type": "text", "text": "\n"})
        combined_rich.append({"type": "text", "text": prefix})
        combined_rich.extend(rich)
    if not lines:
        return None
    return "\n".join(lines), combined_rich


_INTRO_LIST_PROMPT_RE = re.compile(
    r"(如下|以下).{0,24}(奖励|加成|效果|增强|方法|来自|强化)|"
    r"提供以下(效果|加成|增强)|会在以下方面|可以用以下|有以下强化"
)


_SET_PIECE_INTRO_RE = re.compile(
    r"装备全(套|部).*(盔甲|铠甲|护具|套装).*(提供以下(效果|加成)|提供如下(效果|加成)?)|"
    r"全套.*提供以下奖励"
)


def _description_missing_intro_list(item: dict) -> bool:
    """描述以引导句或冒号结尾，但缺少后续列表内容。"""
    text = (item.get("description") or "").strip()
    if not text:
        return False
    if re.search(r"\n· ", text) or re.search(r"\n\d+\. ", text):
        return False
    tail = text.split("\n\n")[-1]
    if _SET_PIECE_INTRO_RE.search(tail):
        return False
    if tail.endswith("：") or tail.endswith(":"):
        if _INTRO_LIST_PROMPT_RE.search(text) or "强化" in tail or "增强" in tail:
            return True
        return False
    if _INTRO_LIST_PROMPT_RE.search(text) and re.search(r"\[\d+\]$", tail):
        return True
    return False


def parse_description_from_soup(soup: BeautifulSoup) -> dict | None:
    """提取 Wiki 正文开头导语（infobox 后、目录/章节前的连续段落）"""
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


_STOP_SET_SECTION_IDS = frozenset(
    {
        "配方",
        "Recipes",
        "Crafting",
        "制作",
        "来源",
        "From",
        "历史",
        "History",
        "成就",
        "Achievements",
        "物品掉落",
        "Item_drop",
    }
)


_SET_TYPE_MARKERS = (
    "盔甲套装",
    "时装套装",
    "Armor set",
    "Armor Set",
    "Vanity set",
    "Vanity Set",
)


def _normalize_type_token(val: str) -> str:
    return re.sub(r"[\s_]+", "", (val or "").lower())


def _is_set_item(item: dict) -> bool:
    for stat in item.get("stats", []):
        if stat.get("label") not in ("类型", "Type"):
            continue
        val = stat.get("value", "")
        if any(marker in val for marker in _SET_TYPE_MARKERS):
            return True
        if _normalize_type_token(val) in ("armorset", "vanityset"):
            return True
    return False


def _is_armor_set_item(item: dict) -> bool:
    return _is_set_item(item)


def _set_page_type(item: dict) -> str:
    for stat in item.get("stats", []):
        if stat.get("label") not in ("类型", "Type"):
            continue
        val = stat.get("value", "")
        compact = _normalize_type_token(val)
        if "时装" in val or "Vanity" in val or compact == "vanityset":
            return "vanity_set"
    return "armor_set"


def parse_piece_infobox(infobox: Tag, fallback_name: str = "") -> dict | None:
    """解析套装面板中的单个部件 infobox"""
    if not infobox:
        return None

    piece: dict = {
        "name": fallback_name,
        "image": "",
        "stats": [],
    }

    title_el = infobox.select_one(".title")
    if title_el:
        piece["name"] = _clean_text(title_el.get_text())

    img_el = infobox.select_one(".section.images img")
    if img_el and img_el.get("src"):
        piece["image"] = _filename_from_url(_image_url_from_src(img_el["src"]))

    for row in infobox.select(".section.statistics table.stat tr"):
        th, td = row.select_one("th"), row.select_one("td")
        if not th or not td:
            continue
        label = _clean_text(th.get_text())
        if label in RARITY_LABELS:
            piece["stats"].append(_parse_rarity_stat(td, label))
            continue
        if label in COIN_STAT_LABELS:
            piece["stats"].append(_parse_sell_stat(td, label))
            continue
        if not label:
            continue
        piece["stats"].append(_parse_generic_stat(td, label))

    if not piece["name"]:
        return None
    return piece


def _collect_set_piece_infoboxes(soup: BeautifulSoup) -> list[Tag]:
    """收集 #套装 区块内的各部件 infobox"""
    set_head = soup.select_one("h2 span#套装") or soup.select_one("h2 span#Set")
    if not set_head:
        return []

    start_h2 = set_head.find_parent("h2")
    if not start_h2:
        return []

    pieces: list[Tag] = []
    in_helmet_section = False

    for sib in start_h2.find_next_siblings():
        if sib.name == "h2":
            hid_el = sib.select_one("span.mw-headline")
            if hid_el and hid_el.get("id") in _STOP_SET_SECTION_IDS:
                break
            continue

        if sib.name == "h3":
            hid_el = sib.select_one("span.mw-headline")
            if hid_el and hid_el.get("id") in ("头盔种类", "Helmet_variants"):
                in_helmet_section = True
                for sib2 in sib.find_next_siblings():
                    if sib2.name == "h2":
                        hid2 = sib2.select_one("span.mw-headline")
                        if hid2 and hid2.get("id") in _STOP_SET_SECTION_IDS:
                            break
                        continue
                    if sib2.name == "h3":
                        continue
                    if sib2.name == "div" and "infobox-wrapper" in (sib2.get("class") or []):
                        pieces.extend(sib2.select("div.infobox.item"))
                break
            continue

        if in_helmet_section:
            continue

        if sib.name == "div" and "infobox-wrapper" in (sib.get("class") or []):
            pieces.extend(sib.select("div.infobox.item"))

    return pieces


def _parse_recipe_row(row: Tag) -> dict | None:
    station_el = row.select_one("td.station")
    station = _clean_text(station_el.get_text()) if station_el else ""
    station = re.sub(r"(?<=[a-zA-Z\)）])or(?=[A-Z])", " or ", station)

    ingredients: list[dict] = []
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
    if not result.get("name") and not ingredients:
        return None
    return {"station": station, "ingredients": ingredients, "result": result}


def parse_recipe_table(table: Tag) -> list[dict]:
    """解析配方表的全部行"""
    if table.select_one("caption"):
        return []
    rows = [tr for tr in table.select("tbody tr") if tr.get("data-rowid")]
    recipes: list[dict] = []
    last_station = ""
    for row in rows:
        parsed = _parse_recipe_row(row)
        if not parsed:
            continue
        if parsed.get("station"):
            last_station = parsed["station"]
        elif last_station:
            parsed["station"] = last_station
        recipes.append(parsed)
    return recipes


def _attach_armor_set_pieces(soup: BeautifulSoup, item: dict) -> None:
    """为盔甲套装页附加各部件数据，并匹配配方"""
    infoboxes = _collect_set_piece_infoboxes(soup)
    if not infoboxes:
        return

    recipes: list[dict] = []
    for table in soup.select("table.terraria.cellborder.recipes"):
        recipes = parse_recipe_table(table)
        if recipes:
            break

    recipe_by_result: dict[str, dict] = {}
    for recipe in recipes:
        rname = recipe.get("result", {}).get("name", "")
        if rname:
            recipe_by_result[rname] = recipe

    set_pieces: list[dict] = []
    for ib in infoboxes:
        piece = parse_piece_infobox(ib)
        if not piece:
            continue
        pname = piece["name"]
        if pname in recipe_by_result:
            piece["recipe"] = recipe_by_result[pname]
        set_pieces.append(piece)

    if not set_pieces:
        return

    item["page_type"] = _set_page_type(item)
    item["set_pieces"] = set_pieces
    item["recipe"] = None


def _piece_page_type(parent_item: dict) -> str:
    if parent_item.get("page_type") == "vanity_set":
        return "vanity_piece"
    return "armor_piece"


def _merge_set_pieces(items: dict[str, dict], set_name: str, set_item: dict) -> int:
    added = 0
    piece_type = _piece_page_type(set_item)
    for piece in set_item.get("set_pieces") or []:
        pname = piece.get("name")
        if not pname:
            continue
        existing = items.get(pname)
        if existing and not existing.get("from_armor_set"):
            continue
        entry: dict = {
            "name": pname,
            "image": piece.get("image", ""),
            "stats": piece.get("stats", []),
            "recipe": piece.get("recipe"),
            "page_type": piece_type,
            "from_armor_set": True,
            "parent_set": set_name,
        }
        items[pname] = entry
        added += 1
    return added


def _merge_armor_set_pieces(items: dict[str, dict], set_name: str, set_item: dict) -> int:
    return _merge_set_pieces(items, set_name, set_item)


def resync_set_piece_locales(items: dict[str, dict]) -> int:
    """从套装父条目同步部件条目。"""
    updated = 0
    for key, item in list(items.items()):
        if item.get("page_type") not in ("armor_set", "vanity_set") and not _is_set_item(item):
            continue
        if not item.get("set_pieces"):
            continue
        updated += _merge_set_pieces(items, key, item)
    return updated


def _migrate_item_images(item: dict) -> int:
    changed = 0

    def fix_image(field: str) -> None:
        nonlocal changed
        raw = item.get(field)
        if not raw:
            return
        normalized = _normalize_image_filename(raw)
        if normalized != raw:
            item[field] = normalized
            changed += 1

    fix_image("image")
    for stat in item.get("stats", []):
        for seg in stat.get("segments", []):
            if seg.get("type") == "icon" and seg.get("image"):
                normalized = _normalize_image_filename(seg["image"])
                if normalized != seg["image"]:
                    seg["image"] = normalized
                    changed += 1
        if stat.get("value_image"):
            normalized = _normalize_image_filename(stat["value_image"])
            if normalized != stat["value_image"]:
                stat["value_image"] = normalized
                changed += 1
    recipe = item.get("recipe")
    if recipe:
        for ing in recipe.get("ingredients", []):
            if ing.get("image"):
                normalized = _normalize_image_filename(ing["image"])
                if normalized != ing["image"]:
                    ing["image"] = normalized
                    changed += 1
        res = recipe.get("result") or {}
        if res.get("image"):
            normalized = _normalize_image_filename(res["image"])
            if normalized != res["image"]:
                res["image"] = normalized
                changed += 1
    for piece in item.get("set_pieces") or []:
        changed += _migrate_item_images(piece)
    return changed


def migrate_item_image_filenames(items: dict[str, dict]) -> int:
    total = 0
    for item in items.values():
        total += _migrate_item_images(item)
    return total


async def refresh_armor_sets(
    session: aiohttp.ClientSession,
    items: dict[str, dict],
) -> int:
    """刷新所有套装页（盔甲/时装）的部件与配方数据"""
    updated = 0
    image_urls: dict[str, str] = {}

    targets: list[tuple[str, str]] = []
    for key, item in items.items():
        if item.get("page_type") in ("armor_set", "vanity_set") or _is_set_item(item):
            title = item.get("wiki_title") or key
            targets.append((key, title))

    for key, title in targets:
        html = await fetch_page_html(session, title)
        if not html:
            continue
        parsed = parse_item_page(html, title)
        if not parsed or not parsed.get("set_pieces"):
            continue
        parsed["wiki_title"] = title
        items[key] = parsed
        updated += 1
        _merge_set_pieces(items, key, parsed)
        image_urls.update(_collect_image_urls(parsed))
        for piece in parsed.get("set_pieces") or []:
            image_urls.update(_collect_image_urls(piece))
        await asyncio.sleep(0.12)

    if updated and image_urls:
        semaphore = asyncio.Semaphore(8)
        await asyncio.gather(
            *[
                download_image(session, fn, url, semaphore)
                for fn, url in image_urls.items()
            ]
        )
    return updated


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

    infobox = _select_item_infobox(soup, fallback_name)
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

    skip_stat_sections = {"声音", "Sounds"}
    for stat_section in infobox.select("div.section.statistics"):
        title_el = stat_section.select_one("div.title")
        section_title = _clean_text(title_el.get_text()) if title_el else ""
        if section_title in skip_stat_sections:
            continue
        for row in stat_section.select("table.stat tr"):
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
        if table.select_one("caption"):
            continue
        if fallback_name in OVERVIEW_PAGE_TITLES or item["name"] in OVERVIEW_PAGE_TITLES:
            break
        recipes = parse_recipe_table(table)
        if not recipes:
            continue
        item["recipe"] = recipes[0]
        break

    drops = parse_drops_from_soup(soup)
    if drops:
        item["drops"] = drops

    parsed = parse_description_from_soup(soup)
    if not _apply_description_to_item(item, parsed):
        _apply_description_fallback(item)

    _apply_overview_page_meta(item, fallback_name)

    if _is_armor_set_item(item):
        _attach_armor_set_pieces(soup, item)

    buff = parse_mount_buff_section(infobox)
    mount = parse_mount_preview_section(infobox)
    pet = parse_pet_preview_section(infobox)
    if pet or _is_pet_summon_item(item):
        item["page_type"] = "pet"
        if buff:
            item["buff"] = buff
        if pet:
            item["pet"] = pet
    elif buff or mount or _is_mount_summon_item(item):
        item["page_type"] = "mount"
        if buff:
            item["buff"] = buff
        if mount:
            item["mount"] = mount

    if item.get("page_type") == "mount":
        _apply_mount_variant_overrides(soup, infobox, item, fallback_name)
    elif item.get("page_type") == "pet":
        _apply_pet_catalog_meta(item, fallback_name)

    return item


def _select_item_infobox(soup: BeautifulSoup, fallback_name: str) -> Tag | None:
    boxes = soup.select("div.infobox.item")
    if not boxes:
        return None
    if fallback_name:
        for box in boxes:
            title_el = box.select_one(".title")
            title = _clean_text(title_el.get_text()) if title_el else ""
            if title == fallback_name:
                return box
    return boxes[0]


_MOUNT_CATALOG_CACHE: dict[str, dict] | None = None


def _mount_overview_html_path() -> str | None:
    path = os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{MOUNT_OVERVIEW_PAGE}.html")
    return path if os.path.isfile(path) else None


def _table_cell_item_name(td: Tag) -> str:
    text_link = td.select_one("span > span > a")
    if text_link:
        return _clean_text(text_link.get_text())
    for a in td.select("a"):
        title = _clean_text(a.get("title") or "")
        if title:
            return title
        alt = _clean_text(a.get("alt") or "")
        if alt:
            return alt
    sp = td.select_one("span[title]")
    if sp:
        return _clean_text(sp.get("title", ""))
    return _clean_text(td.get_text())


def _table_cell_wiki_title(td: Tag) -> str:
    """物品栏链接的 Wiki 页面标题（优先 title 属性，避免消歧义页）。"""
    text_link = td.select_one("span > span > a")
    if text_link:
        title = _clean_text(text_link.get("title") or "")
        if title:
            return title
        return _clean_text(text_link.get_text())
    for a in td.select("a"):
        title = _clean_text(a.get("title") or "")
        if title:
            return title
    return _table_cell_item_name(td)


def load_mount_overview_catalog() -> dict[str, dict]:
    """坐骑总览页「物品」栏：item_name -> {mount_display, mount_image}"""
    global _MOUNT_CATALOG_CACHE
    if _MOUNT_CATALOG_CACHE is not None:
        return _MOUNT_CATALOG_CACHE

    catalog: dict[str, dict] = {}
    path = _mount_overview_html_path()
    if not path:
        _MOUNT_CATALOG_CACHE = catalog
        return catalog

    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")
    seen: set[tuple[str, str]] = set()
    for table in soup.select("table.terraria.lined"):
        for tr in table.select("tr")[1:]:
            tds = tr.select("td")
            if len(tds) < 2:
                continue
            mount_display = _table_cell_item_name(tds[0])
            item_name = _table_cell_item_name(tds[1])
            if not mount_display or not item_name:
                continue
            key = (mount_display, item_name)
            if key in seen:
                continue
            seen.add(key)
            mount_img_el = tds[0].select_one("img")
            mount_image = ""
            if mount_img_el and mount_img_el.get("src"):
                mount_image = _filename_from_url(
                    _image_url_from_src(mount_img_el["src"])
                )
            catalog[item_name] = {
                "mount_display": mount_display,
                "mount_image": mount_image,
            }

    _MOUNT_CATALOG_CACHE = catalog
    return catalog


def load_mount_summon_item_titles() -> list[str]:
    return sorted(load_mount_overview_catalog().keys())


def _row_item_name(tr: Tag) -> str | None:
    anchor = tr.select_one("s.anchor")
    if anchor and anchor.get("id"):
        return _clean_text(anchor["id"])
    il2 = tr.select_one("td.il2c span[title]")
    if il2:
        return _clean_text(il2.get("title", ""))
    return None


def parse_mount_variant_row(soup: BeautifulSoup, item_name: str) -> dict | None:
    """解析合并页（马鞍/轮滑鞋等）种类表中的单个召唤物。"""
    for tr in soup.select("table.terraria.lined tr, table.terraria.sortable.lined tr"):
        if _row_item_name(tr) != item_name:
            continue
        result: dict = {}
        img = tr.select_one("td.il1c img") or tr.select_one("img")
        if img and img.get("src"):
            result["image"] = _filename_from_url(_image_url_from_src(img["src"]))
        tds = tr.select("td")
        if len(tds) >= 5:
            mount_img = tds[2].select_one("img")
            buff_block = tds[3]
            tooltip_el = tds[4].select_one("i")
            buff_name = ""
            sp = buff_block.select_one("span[title]")
            if sp:
                buff_name = _clean_text(sp.get("title", ""))
            buff_img = buff_block.select_one("img")
            result["buff"] = {
                "name": buff_name,
                "image": (
                    _filename_from_url(_image_url_from_src(buff_img["src"]))
                    if buff_img and buff_img.get("src")
                    else ""
                ),
                "tooltip": _clean_text(tooltip_el.get_text()) if tooltip_el else "",
            }
            result["mount"] = {
                "name": buff_name,
                "image": (
                    _filename_from_url(_image_url_from_src(mount_img["src"]))
                    if mount_img and mount_img.get("src")
                    else ""
                ),
            }
        return result or None
    return None


def parse_mount_buff_for_name(infobox: Tag, buff_name: str) -> dict | None:
    section = infobox.select_one("div.section.buff")
    if not section:
        return None
    for table in section.select("table.stat"):
        buff = {"name": "", "image": "", "tooltip": ""}
        for row in table.select("tr"):
            th, td = row.select_one("th"), row.select_one("td")
            if not th or not td:
                continue
            label = _clean_text(th.get_text())
            if label in ("增益", "Buff"):
                img = td.select_one("img")
                if img and img.get("src"):
                    buff["image"] = _filename_from_url(_image_url_from_src(img["src"]))
                name_link = (
                    td.select_one("a.mw-selflink")
                    or td.select_one("span > span > a")
                    or td.select_one("a")
                )
                buff["name"] = _clean_text(
                    name_link.get_text() if name_link else td.get_text()
                )
            elif label in ("增益提示", "Buff tooltip"):
                tooltip_el = td.select_one("i") or td
                buff["tooltip"] = _clean_text(tooltip_el.get_text())
        if buff["name"] == buff_name:
            return buff
    return None


def _mount_image_from_buff_image(buff_image: str) -> str:
    if not buff_image:
        return ""
    if "(buff)" in buff_image.lower():
        return buff_image.replace("(buff)", "(mount)").replace("(Buff)", "(mount)")
    stem, ext = os.path.splitext(buff_image)
    return f"{stem}_(mount){ext}"


def _apply_mount_catalog_search(item: dict, catalog_entry: dict) -> None:
    mount_display = catalog_entry.get("mount_display", "")
    if not mount_display:
        return
    terms = list(item.get("search_terms") or [])
    for alias in (mount_display, f"{mount_display}坐骑"):
        if alias and alias not in terms and alias != item.get("name"):
            terms.append(alias)
    item["search_terms"] = terms


def _apply_mount_variant_overrides(
    soup: BeautifulSoup,
    infobox: Tag,
    item: dict,
    fallback_name: str,
) -> None:
    if not fallback_name:
        return

    catalog = load_mount_overview_catalog()
    catalog_entry = catalog.get(fallback_name)

    if item.get("name") != fallback_name:
        variant = parse_mount_variant_row(soup, fallback_name)
        named_buff = parse_mount_buff_for_name(infobox, fallback_name)
        if variant or named_buff or catalog_entry:
            item["name"] = fallback_name
            item["page_type"] = "mount"
        if variant and variant.get("image"):
            item["image"] = variant["image"]
        if named_buff:
            item["buff"] = named_buff
            mount_image = (
                (catalog_entry or {}).get("mount_image")
                or _mount_image_from_buff_image(named_buff.get("image", ""))
            )
            item["mount"] = {"name": fallback_name, "image": mount_image}
        elif variant:
            if variant.get("buff"):
                item["buff"] = variant["buff"]
            if variant.get("mount"):
                item["mount"] = variant["mount"]

    if catalog_entry:
        _apply_mount_catalog_search(item, catalog_entry)
        if catalog_entry.get("mount_image") and item.get("mount"):
            item["mount"]["image"] = catalog_entry["mount_image"]


def _prune_mounts_to_catalog(mounts: dict[str, dict]) -> dict[str, dict]:
    catalog = load_mount_overview_catalog()
    allowed = set(catalog) - MOUNT_CATALOG_EXCLUDE
    pruned = {k: v for k, v in mounts.items() if k in allowed}
    for title, meta in catalog.items():
        if title in pruned:
            _apply_mount_catalog_search(pruned[title], meta)
            if meta.get("mount_image") and pruned[title].get("mount"):
                pruned[title]["mount"]["image"] = meta["mount_image"]
    return pruned


_PET_CATALOG_CACHE: dict[str, dict] | None = None
_PET_SUPPLEMENTAL_CACHE: dict[str, dict] | None = None


def _wiki_mirror_page_path(title: str) -> str | None:
    path = os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{title}.html")
    return path if os.path.isfile(path) else None


def _wiki_titles_in_element(root: Tag) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for a in root.select('a[href^="/zh/wiki/"]'):
        href = a.get("href", "")
        match = re.match(r"/zh/wiki/([^?#]+)", href)
        if not match:
            continue
        title = unquote(match.group(1))
        if title.startswith(("Category:", "File:")) or title in seen:
            continue
        seen.add(title)
        titles.append(title)
    return titles


def _crossover_pet_candidate_titles() -> list[str]:
    """联动物品页中「宠物」段落与帕鲁章节的 Wiki 链接。"""
    titles: list[str] = []
    seen: set[str] = set()
    for page_name in PET_SUPPLEMENT_OVERVIEW_PAGES:
        path = _wiki_mirror_page_path(page_name)
        if not path:
            continue
        with open(path, encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "lxml")
        for p in soup.select("p"):
            if "宠物" not in p.get_text():
                continue
            ul = p.find_next_sibling("ul")
            if not ul:
                continue
            for title in _wiki_titles_in_element(ul):
                if title not in seen:
                    seen.add(title)
                    titles.append(title)
        pal_heading = soup.select_one("#幻兽帕鲁")
        if pal_heading:
            h2 = pal_heading.find_parent("h2")
            if h2:
                ul = h2.find_next_sibling("ul")
                if ul:
                    for title in _wiki_titles_in_element(ul):
                        if title not in seen:
                            seen.add(title)
                            titles.append(title)
    return titles


def _qualifies_as_supplemental_pet(item: dict) -> bool:
    if item.get("page_type") == "mount":
        return False
    if _is_pet_summon_item(item):
        return True
    desc = item.get("description") or ""
    if "宠物物品" in desc or "宠物召唤物品" in desc:
        return True
    pet = item.get("pet") or {}
    buff = item.get("buff") or {}
    if pet and buff and "帕鲁" in desc:
        return True
    return False


def _load_supplemental_pet_catalog() -> dict[str, dict]:
    """总览表未收录、但 Wiki 明确为宠物的联动等内容。"""
    global _PET_SUPPLEMENTAL_CACHE
    if _PET_SUPPLEMENTAL_CACHE is not None:
        return _PET_SUPPLEMENTAL_CACHE

    catalog: dict[str, dict] = {}
    existing = _PET_CATALOG_CACHE or {}
    for title in _crossover_pet_candidate_titles():
        page_path = _wiki_mirror_page_path(title)
        if not page_path:
            continue
        with open(page_path, encoding="utf-8") as f:
            item = parse_item_page(f.read(), title)
        if not item or not _qualifies_as_supplemental_pet(item):
            continue
        item_name = item.get("name") or title
        if item_name in existing:
            continue
        pet = item.get("pet") or {}
        buff = item.get("buff") or {}
        pet_image = buff.get("image") or pet.get("image") or ""
        catalog[item_name] = {
            "pet_display": pet.get("name") or buff.get("name") or item_name,
            "pet_image": pet_image,
            "light_pet": False,
            "wiki_page": title,
        }

    _PET_SUPPLEMENTAL_CACHE = catalog
    return catalog


def _pet_overview_html_path() -> str | None:
    path = os.path.join(_WIKI_MIRROR_DIR, "wiki", "zh", "pages", f"{PET_OVERVIEW_PAGE}.html")
    return path if os.path.isfile(path) else None


def load_pet_overview_catalog() -> dict[str, dict]:
    """宠物总览页「物品」栏：item_name -> {pet_display, pet_image, light_pet}"""
    global _PET_CATALOG_CACHE
    if _PET_CATALOG_CACHE is not None:
        return _PET_CATALOG_CACHE

    catalog: dict[str, dict] = {}
    path = _pet_overview_html_path()
    if not path:
        _PET_CATALOG_CACHE = catalog
        return catalog

    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    for table_id in PET_TABLE_IDS:
        table = soup.select_one(f"#{table_id}")
        if not table:
            continue
        light_table = table_id in ("table-Light-Pets", "table-Master-Mode-Light-Pets")
        for tr in table.select("tr")[1:]:
            tds = tr.select("td")
            if len(tds) < 3:
                continue
            pet_display = _table_cell_item_name(tds[1])
            item_name = _table_cell_item_name(tds[2])
            wiki_page = _table_cell_wiki_title(tds[2])
            if not item_name:
                continue
            pet_img_el = tds[1].select_one("img")
            pet_image = ""
            if pet_img_el and pet_img_el.get("src"):
                pet_image = _filename_from_url(
                    _image_url_from_src(pet_img_el["src"])
                )
            provides_light = light_table
            if len(tds) >= 5 and not light_table:
                cell_text = _clean_text(tds[4].get_text())
                provides_light = "✔" in cell_text
            catalog[item_name] = {
                "pet_display": pet_display or item_name,
                "pet_image": pet_image,
                "light_pet": provides_light,
                "wiki_page": wiki_page or item_name,
            }

    _PET_CATALOG_CACHE = catalog
    for item_name, meta in _load_supplemental_pet_catalog().items():
        if item_name not in catalog:
            catalog[item_name] = meta

    return catalog


def load_pet_summon_item_titles() -> list[str]:
    return sorted(load_pet_overview_catalog().keys())


def _pet_fetch_tasks(catalog: dict[str, dict]) -> list[tuple[str, str]]:
    """(catalog_key, wiki_page) 列表，用于抓取宠物召唤物页面。"""
    tasks: list[tuple[str, str]] = []
    for key, meta in sorted(catalog.items()):
        wiki_page = meta.get("wiki_page") or key
        tasks.append((key, wiki_page))
    return tasks


def _apply_pet_catalog_search(item: dict, catalog_entry: dict) -> None:
    pet_display = catalog_entry.get("pet_display", "")
    if not pet_display:
        return
    terms = list(item.get("search_terms") or [])
    buff_name = (item.get("buff") or {}).get("name", "")
    for alias in (pet_display, buff_name):
        if alias and alias not in terms and alias != item.get("name"):
            terms.append(alias)
    item["search_terms"] = terms
    if catalog_entry.get("light_pet"):
        item["light_pet"] = True


def _apply_pet_catalog_meta(item: dict, fallback_name: str) -> None:
    catalog_entry = load_pet_overview_catalog().get(fallback_name)
    if not catalog_entry:
        return
    _apply_pet_catalog_search(item, catalog_entry)
    if catalog_entry.get("pet_image") and item.get("pet"):
        item["pet"]["image"] = catalog_entry["pet_image"]
    elif catalog_entry.get("pet_image") and not item.get("pet"):
        item["pet"] = {
            "name": catalog_entry.get("pet_display", fallback_name),
            "image": catalog_entry["pet_image"],
        }


def _prune_pets_to_catalog(pets: dict[str, dict]) -> dict[str, dict]:
    catalog = load_pet_overview_catalog()
    pruned = {k: v for k, v in pets.items() if k in catalog}
    for title, meta in catalog.items():
        if title in pruned:
            _apply_pet_catalog_search(pruned[title], meta)
            if meta.get("pet_image") and pruned[title].get("pet"):
                pruned[title]["pet"]["image"] = meta["pet_image"]
    return pruned


def _is_mount_summon_item(item: dict) -> bool:
    for stat in item.get("stats", []):
        if stat.get("label") not in ("类型", "Type"):
            continue
        parts = [stat.get("value") or ""]
        for seg in stat.get("segments") or []:
            if seg.get("type") == "text":
                parts.append(seg.get("text") or "")
        text = "".join(parts)
        if "坐骑召唤" in text or "Mount summon" in text.lower():
            return True
    return False


def parse_mount_buff_section(infobox: Tag) -> dict | None:
    section = infobox.select_one("div.section.buff")
    if not section:
        return None
    if len(section.select("table.stat")) > 1:
        return None
    buff = {"name": "", "image": "", "tooltip": ""}
    for row in section.select("table.stat tr"):
        th, td = row.select_one("th"), row.select_one("td")
        if not th or not td:
            continue
        label = _clean_text(th.get_text())
        if label in ("增益", "Buff"):
            img = td.select_one("img")
            if img and img.get("src"):
                buff["image"] = _filename_from_url(_image_url_from_src(img["src"]))
            name_link = (
                td.select_one("a.mw-selflink")
                or td.select_one("span > span > a")
                or td.select_one("a")
            )
            if name_link:
                buff["name"] = _clean_text(name_link.get_text())
            else:
                buff["name"] = _clean_text(td.get_text())
        elif label in ("增益提示", "Buff tooltip"):
            tooltip_el = td.select_one("i") or td
            buff["tooltip"] = _clean_text(tooltip_el.get_text())
    return buff if (buff["name"] or buff["tooltip"]) else None


def parse_mount_preview_section(infobox: Tag) -> dict | None:
    section = infobox.select_one("div.section.mount")
    if not section:
        return None
    name_el = section.select_one("div.name")
    img_el = section.select_one("div.image img")
    mount = {
        "name": _clean_text(name_el.get_text()) if name_el else "",
        "image": "",
    }
    if img_el and img_el.get("src"):
        mount["image"] = _filename_from_url(_image_url_from_src(img_el["src"]))
    return mount if (mount["name"] or mount["image"]) else None


def parse_pet_preview_section(infobox: Tag) -> dict | None:
    section = infobox.select_one("div.section.projectile")
    if not section:
        return None
    name_el = section.select_one("div.name")
    img_el = section.select_one("div.image img")
    pet = {
        "name": _clean_text(name_el.get_text()) if name_el else "",
        "image": "",
    }
    if img_el and img_el.get("src"):
        pet["image"] = _filename_from_url(_image_url_from_src(img_el["src"]))
    return pet if (pet["name"] or pet["image"]) else None


def _is_pet_summon_item(item: dict) -> bool:
    for stat in item.get("stats", []):
        if stat.get("label") not in ("类型", "Type"):
            continue
        parts = [stat.get("value") or ""]
        for seg in stat.get("segments") or []:
            if seg.get("type") == "text":
                parts.append(seg.get("text") or "")
        text = "".join(parts)
        if "宠物召唤" in text or "Pet summon" in text.lower():
            return True
        if "照明宠物" in text:
            return True
    return False


def strip_english_fields(items: dict[str, dict]) -> int:
    """移除 en / en_name / aliases 等英文字段。"""
    stripped = 0
    for item in items.values():
        touched = False
        for field in ("en", "en_name", "aliases"):
            if field in item:
                item.pop(field, None)
                touched = True
        for piece in item.get("set_pieces") or []:
            if not isinstance(piece, dict):
                continue
            for field in ("en", "en_name", "aliases"):
                piece.pop(field, None)
        if touched:
            stripped += 1
    return stripped


# 兼容旧调用名

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
        _persist_items(items)

    return found, image_urls


def _relocate_misplaced_en_description(item: dict) -> bool:
    """中文物品顶层误存英文描述时清空，等待重新抓取中文。"""
    desc = item.get("description") or ""
    name = item.get("name") or ""
    if not desc or _has_cjk(desc) or not _has_cjk(name):
        return False
    item.pop("description", None)
    item.pop("description_rich", None)
    return True


def _description_needs_zh_refresh(item: dict) -> bool:
    """中文物品缺少描述，或顶层描述实为英文 / 仅工具提示。"""
    desc = item.get("description") or ""
    name = item.get("name") or ""
    if not desc:
        return True
    if _description_missing_intro_list(item):
        return True
    if _has_cjk(name) and not _has_cjk(desc):
        return True
    if _description_is_tooltip_only(item):
        return True
    return _description_needs_coin_refresh(item)


def _description_needs_coin_refresh(item: dict) -> bool:
    """旧描述只存了金额数字、未解析金币图标时需重新抓取。"""
    text = item.get("description") or ""
    if not any(token in text for token in ("购买", "出售", "price", "buy", "sell", "purchase")):
        return False
    for para in item.get("description_rich") or []:
        for seg in para:
            if seg.get("type") == "coin":
                return False
    return bool(re.search(r"\d", text))


async def backfill_descriptions(
    session: aiohttp.ClientSession,
    items: dict[str, dict],
    force: bool = False,
    limit: int | None = None,
) -> int:
    pending = [
        key
        for key, item in items.items()
        if force or _description_needs_zh_refresh(item)
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
        _persist_items(items)

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
    """通过 MediaWiki API 获取页面 HTML；优先读本地镜像。"""
    locale = api_url_to_locale(api_url)
    cached = _load_mirror_html(title, locale)
    if cached:
        return cached

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
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "").strip()
                    wait = (
                        float(retry_after)
                        if retry_after.isdigit()
                        else 3.0 * (attempt + 1)
                    )
                    logger.warning(
                        f"Wiki 429 限速 ({title})，等待 {wait:.0f}s 后重试…"
                    )
                    await asyncio.sleep(wait)
                    continue
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
    for piece in item.get("set_pieces") or []:
        urls.update(_collect_image_urls(piece))
    buff = item.get("buff")
    if buff and buff.get("image"):
        fn = buff["image"]
        urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    mount = item.get("mount")
    if mount and mount.get("image"):
        fn = mount["image"]
        urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    pet = item.get("pet")
    if pet and pet.get("image"):
        fn = pet["image"]
        urls[fn] = f"https://terraria.wiki.gg/images/{quote(fn, safe='')}"
    return urls


def _apply_overview_page_meta(item: dict, wiki_title: str) -> None:
    if wiki_title not in OVERVIEW_PAGES and item.get("name", "") not in OVERVIEW_PAGES:
        return
    item["page_type"] = "overview"
    item["recipe"] = None
    if wiki_title:
        item["wiki_title"] = wiki_title


def _wing_search_terms(name: str) -> list[str]:
    terms: list[str] = []
    if name.endswith("之翼") and len(name) > 2:
        terms.append(name[:-2])
    return terms


def _format_wing_flight_time(raw: str, *, locale: str = "zh") -> str:
    raw = _clean_text(raw)
    if not raw:
        return ""
    match = re.match(r"^([\d.]+)\s*s\s*$", raw, re.I)
    if not match:
        return raw
    if locale == "zh":
        return f"{match.group(1)}秒"
    return f"{match.group(1)} s"


def _format_wing_height(raw: str, *, locale: str = "zh") -> str:
    raw = _clean_text(raw)
    if not raw:
        return ""
    if re.fullmatch(r"\d+", raw):
        return f"{raw}格" if locale == "zh" else raw
    return raw


def _format_wing_mph(raw: str, *, locale: str = "zh") -> str:
    raw = _clean_text(raw)
    if not raw:
        return ""
    match = re.match(r"^([\d.]+)\s*mph\s*$", raw, re.I)
    num = match.group(1) if match else raw if re.fullmatch(r"[\d.]+", raw) else ""
    if not num:
        return raw
    return f"{num} mph"


def _rich_from_wing_note_li(li: Tag) -> list[dict]:
    """从翅膀备注 li 解析富文本（保留 Wiki span.key 结构）"""
    if li.select("span.key"):
        return _parse_description_paragraph_rich(li)
    text = _clean_text(li.get_text(" ", strip=True))
    if not text:
        return []
    paras = description_text_to_rich(text)
    return paras[0] if paras else [{"type": "text", "text": text}]


def _parse_wing_source_cell(
    cell: Tag,
    *,
    locale: str = "zh",
    wing_name: str = "",
    wing_image: str = "",
) -> dict:
    """解析翅膀对比表「来源」列：合成材料或文字说明。"""
    parsed: dict = {}
    if not cell:
        return parsed

    ingredients_root = cell.select_one("div.recipes div.ingredients")
    if not ingredients_root:
        ingredients_root = cell.select_one("div.ingredients")
    ingredients: list[dict] = []
    for li in cell.select("ul li"):
        span_i = li.select_one("span.i")
        if not span_i:
            continue
        ing_name, ing_image = _parse_item_span(span_i)
        if not ing_name:
            continue
        entry: dict = {"name": ing_name, "image": ing_image}
        amount_el = li.select_one("span.am")
        amount = _clean_text(amount_el.get_text()) if amount_el else ""
        if amount and amount not in ("1", ""):
            entry["amount"] = amount
        ingredients.append(entry)

    if ingredients:
        station = ""
        if ingredients_root:
            prefix_parts: list[str] = []
            for child in ingredients_root.children:
                if getattr(child, "name", None) == "ul":
                    break
                if getattr(child, "name", None) == "a" and child.get("title"):
                    title = _clean_text(child.get("title"))
                    if title:
                        prefix_parts.append(title)
                elif isinstance(child, str):
                    text = _clean_text(child)
                    if text and text not in ("：", ":"):
                        prefix_parts.append(text)
            if prefix_parts:
                station = "、".join(prefix_parts)
        parsed["recipe"] = {
            "station": station,
            "ingredients": ingredients,
            "result": {"name": wing_name, "image": wing_image},
        }
        return parsed

    rich = _parse_description_paragraph_rich(cell)
    text = _rich_segments_to_text(rich) if rich else _clean_text(cell.get_text(" ", strip=True))
    if text:
        parsed["source"] = text
        parsed["source_rich"] = [rich] if rich else description_text_to_rich(text)
    return parsed


def _parse_wing_row(tr: Tag, *, locale: str = "zh") -> dict | None:
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
    source_cell = cells[3] if len(cells) > 3 else None
    source_parsed = _parse_wing_source_cell(
        source_cell,
        locale=locale,
        wing_name=name,
        wing_image=image,
    )
    flight_time = (
        _format_wing_flight_time(cells[4].get_text(" ", strip=True), locale=locale)
        if len(cells) > 4
        else ""
    )
    height = (
        _format_wing_height(cells[5].get_text(" ", strip=True), locale=locale)
        if len(cells) > 5
        else ""
    )
    mph = (
        _format_wing_mph(cells[6].get_text(" ", strip=True), locale=locale)
        if len(cells) > 6
        else ""
    )
    h_accel = _clean_text(cells[7].get_text(" ", strip=True)) if len(cells) > 7 else ""
    v_mult = _clean_text(cells[8].get_text(" ", strip=True)) if len(cells) > 8 else ""

    rarity_label = "稀有度" if locale == "zh" else "Rarity"
    rarity_stat = (
        _parse_rarity_stat(cells[9], rarity_label) if len(cells) > 9 else None
    )

    notes: list[str] = []
    note_rich_paragraphs: list[list[dict]] = []
    note_cell = cells[10] if len(cells) > 10 else tr
    for li in note_cell.select("ul li"):
        note = _clean_text(li.get_text(" ", strip=True))
        if note:
            notes.append(note)
            note_rich_paragraphs.append(_rich_from_wing_note_li(li))

    type_label = "类型" if locale == "zh" else "Type"
    type_value = "配饰" if locale == "zh" else "Accessory"
    flight_label = "飞行时间" if locale == "zh" else "Flight time"
    height_label = "高度（格）" if locale == "zh" else "Height (tiles)"
    mph_label = "最大水平速度" if locale == "zh" else "Max horizontal speed"
    h_accel_label = "水平加速度" if locale == "zh" else "Horizontal acceleration"
    v_mult_label = "垂直倍率" if locale == "zh" else "Vertical multiplier"

    stats: list[dict] = [{"label": type_label, "value": type_value, "extra": ""}]
    if flight_time:
        stats.append({"label": flight_label, "value": flight_time, "extra": ""})
    if height:
        stats.append({"label": height_label, "value": height, "extra": ""})
    if mph:
        stats.append({"label": mph_label, "value": mph, "extra": ""})
    if h_accel:
        stats.append({"label": h_accel_label, "value": h_accel, "extra": ""})
    if v_mult:
        stats.append({"label": v_mult_label, "value": v_mult, "extra": ""})
    if rarity_stat:
        stats.append(rarity_stat)

    desc_parts: list[str] = list(notes)
    note_rich_paragraphs = list(note_rich_paragraphs)

    item: dict = {
        "name": name,
        "image": image,
        "stats": stats,
        "recipe": source_parsed.get("recipe"),
        "page_type": "wing",
        "from_wings_table": True,
        "parent_page": "翅膀",
    }
    if source_parsed.get("source"):
        item["source"] = source_parsed["source"]
    if source_parsed.get("source_rich"):
        item["source_rich"] = source_parsed["source_rich"]
    if desc_parts:
        item["description"] = "\n\n".join(desc_parts)
        item["description_rich"] = note_rich_paragraphs
    terms = _wing_search_terms(name)
    if terms:
        item["search_terms"] = terms
    return item


def parse_wings_from_soup(soup: BeautifulSoup, *, locale: str = "zh") -> dict[str, dict]:
    """从「翅膀」总览页的对比表中解析各翅膀条目"""
    wings: dict[str, dict] = {}
    for table in soup.select("table.terraria"):
        anchors = table.select("s.anchor[id]")
        if len(anchors) < 10:
            continue
        for tr in table.select("tbody tr"):
            wing = _parse_wing_row(tr, locale=locale)
            if wing:
                wings[wing["name"]] = wing
        if wings:
            break
    return wings


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
        items[name] = item
        updated += 1

        zh_wings = parse_wings_from_soup(soup)
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
        _persist_items(items)
    return updated


def _load_existing_items() -> dict[str, dict]:
    return _category_data_module().load_items_for_plugin(CATEGORIES_DIR)


def _load_existing_mounts() -> dict[str, dict]:
    if not os.path.exists(MOUNTS_JSON):
        return {}
    try:
        with open(MOUNTS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _existing_mount_wiki_titles(mounts: dict[str, dict]) -> set[str]:
    titles: set[str] = set()
    for item in mounts.values():
        wt = item.get("wiki_title")
        if wt:
            titles.add(wt)
        name = item.get("name")
        if name:
            titles.add(name)
    return titles


def _load_existing_pets() -> dict[str, dict]:
    if not os.path.exists(PETS_JSON):
        return {}
    try:
        with open(PETS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _existing_pet_wiki_titles(pets: dict[str, dict]) -> set[str]:
    titles: set[str] = set()
    for item in pets.values():
        wt = item.get("wiki_title")
        if wt:
            titles.add(wt)
        name = item.get("name")
        if name:
            titles.add(name)
    return titles


async def update_wiki_data(
    limit: int | None = None,
    force: bool = False,
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
    drops_backfill_count = 0
    desc_backfill_count = 0
    overview_count = 0
    image_migrate_count = 0
    piece_sync_count = 0
    strip_count = 0
    mount_result = {"new_count": 0, "total": len(_load_existing_mounts())}
    pet_result = {"new_count": 0, "total": len(_load_existing_pets())}
    biome_result = {
        "new_count": 0,
        "total": len(_biome_data_module().load_biomes_for_plugin(CATEGORIES_DIR)),
    }
    npc_result = {
        "new_count": 0,
        "total": len(_npc_data_module().load_npcs_for_plugin(CATEGORIES_DIR)),
    }

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
        else:
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
                _apply_search_aliases(item)
                items[name] = item
                if _is_set_item(item):
                    _merge_set_pieces(items, name, item)
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
            armor_set_count = await refresh_armor_sets(session, items)
            overview_count += armor_set_count

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

        mount_result = await refresh_mounts(session, force=force)
        pet_result = await refresh_pets(session, force=force)
        biome_result = await _biome_data_module().refresh_biomes(session, force=force)
        npc_result = await _npc_data_module().refresh_npcs(session, force=force)

        strip_count = strip_english_fields(items)
        image_migrate_count = migrate_item_image_filenames(items)
        piece_sync_count = resync_set_piece_locales(items)
        await _persist_items_async(session, items)

    return {
        "ok": True,
        "force": force,
        "before_total": before_total,
        "new_count": new_count,
        "total": len(items),
        "pages_scanned": pages_scanned,
        "images_total": images_total,
        "images_ok": images_ok,
        "en_stripped_count": strip_count,
        "drops_backfill_count": drops_backfill_count,
        "desc_backfill_count": desc_backfill_count,
        "overview_count": overview_count,
        "image_migrate_count": image_migrate_count,
        "piece_sync_count": piece_sync_count,
        "mount_new_count": mount_result.get("new_count", 0),
        "mount_total": mount_result.get("total", 0),
        "pet_new_count": pet_result.get("new_count", 0),
        "pet_total": pet_result.get("total", 0),
        "biome_new_count": biome_result.get("new_count", 0),
        "biome_total": biome_result.get("total", 0),
        "npc_new_count": npc_result.get("new_count", 0),
        "npc_total": npc_result.get("total", 0),
    }


def _apply_search_aliases(item: dict) -> None:
    name = item.get("name", "")
    aliases = ITEM_SEARCH_ALIASES.get(name)
    if aliases:
        existing = list(item.get("search_terms") or [])
        for alias in aliases:
            if alias not in existing:
                existing.append(alias)
        item["search_terms"] = existing


async def ingest_mount_titles(
    session: aiohttp.ClientSession,
    mounts: dict[str, dict],
    title_list: list[str],
    *,
    force: bool = False,
) -> tuple[int, int, int]:
    """抓取坐骑召唤物页面，返回 (新增数, 图片成功数, 图片总数)。"""
    catalog_titles = load_mount_summon_item_titles()
    if catalog_titles:
        title_list = catalog_titles
    if force:
        pending = list(title_list)
    else:
        existing = _existing_mount_wiki_titles(mounts)
        pending = [t for t in title_list if t not in existing]
    pending.sort()
    image_urls: dict[str, str] = {}
    new_count = 0

    for i, title in enumerate(pending, 1):
        if title in MOUNT_CATALOG_EXCLUDE:
            continue
        html = await fetch_page_html(session, title)
        if not html:
            continue
        item = parse_item_page(html, title)
        if not item:
            continue
        if item.get("page_type") != "mount" and not _is_mount_summon_item(item):
            if title not in load_mount_overview_catalog():
                continue
            item["page_type"] = "mount"
        name = item.get("name") or title
        if name != title:
            item["name"] = title
            name = title
        if not force and name in mounts:
            continue
        item["wiki_title"] = title
        _apply_search_aliases(item)
        catalog_entry = load_mount_overview_catalog().get(title)
        if catalog_entry:
            _apply_mount_catalog_search(item, catalog_entry)
        mounts[name] = item
        image_urls.update(_collect_image_urls(item))
        new_count += 1
        if i % 10 == 0 or i == len(pending):
            print(
                f"坐骑抓取 {i}/{len(pending)}，新增 {new_count}，总计 {len(mounts)}",
                flush=True,
            )
        await asyncio.sleep(0.12)

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

    strip_english_fields(mounts)
    migrate_item_image_filenames(mounts)
    pruned = _prune_mounts_to_catalog(mounts)
    mounts.clear()
    mounts.update(pruned)
    return new_count, images_ok, len(image_urls)


async def refresh_mounts(
    session: aiohttp.ClientSession,
    *,
    force: bool = False,
) -> dict:
    """更新 mounts.json（以坐骑总览页物品栏为准，共 37 种召唤物）。"""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    mounts: dict[str, dict] = {} if force else _load_existing_mounts()
    before = len(mounts)

    catalog = load_mount_overview_catalog()
    if catalog:
        print(f"坐骑总览页：{len(catalog)} 种召唤物品", flush=True)
        title_list = load_mount_summon_item_titles()
    else:
        titles = await fetch_category_members(session, MOUNT_CATEGORY)
        print(f"{MOUNT_CATEGORY}：{len(titles)} 个标题（未找到本地总览页）", flush=True)
        title_list = sorted(titles)

    new_count, images_ok, images_total = await ingest_mount_titles(
        session,
        mounts,
        title_list,
        force=force,
    )
    with open(MOUNTS_JSON, "w", encoding="utf-8") as f:
        json.dump(mounts, f, ensure_ascii=False, indent=2)

    return {
        "before": before,
        "new_count": new_count,
        "total": len(mounts),
        "images_ok": images_ok,
        "images_total": images_total,
    }


async def ingest_pet_titles(
    session: aiohttp.ClientSession,
    pets: dict[str, dict],
    title_list: list[str],
    *,
    force: bool = False,
) -> tuple[int, int, int]:
    """抓取宠物召唤物页面，返回 (新增数, 图片成功数, 图片总数)。"""
    catalog = load_pet_overview_catalog()
    fetch_tasks = _pet_fetch_tasks(catalog) if catalog else []
    if fetch_tasks:
        pending_tasks = fetch_tasks
    elif title_list:
        pending_tasks = [(t, t) for t in title_list]
    else:
        pending_tasks = []

    if force:
        pending = list(pending_tasks)
    else:
        existing = _existing_pet_wiki_titles(pets)
        pending = [
            (key, wiki_page)
            for key, wiki_page in pending_tasks
            if key not in pets and wiki_page not in existing
        ]
    image_urls: dict[str, str] = {}
    new_count = 0

    for i, (catalog_key, wiki_page) in enumerate(pending, 1):
        html = await fetch_page_html(session, wiki_page)
        if not html:
            continue
        item = parse_item_page(html, wiki_page)
        if not item:
            continue
        if item.get("page_type") != "pet" and not _is_pet_summon_item(item):
            if catalog_key not in catalog:
                continue
            item["page_type"] = "pet"
        name = catalog_key
        if not force and name in pets:
            continue
        item["name"] = name
        item["wiki_title"] = wiki_page
        _apply_search_aliases(item)
        catalog_entry = catalog.get(catalog_key)
        if catalog_entry:
            _apply_pet_catalog_search(item, catalog_entry)
        pets[name] = item
        image_urls.update(_collect_image_urls(item))
        new_count += 1
        if i % 15 == 0 or i == len(pending):
            print(
                f"宠物抓取 {i}/{len(pending)}，新增 {new_count}，总计 {len(pets)}",
                flush=True,
            )
        await asyncio.sleep(0.12)

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

    strip_english_fields(pets)
    migrate_item_image_filenames(pets)
    pruned = _prune_pets_to_catalog(pets)
    pets.clear()
    pets.update(pruned)
    return new_count, images_ok, len(image_urls)


async def refresh_pets(
    session: aiohttp.ClientSession,
    *,
    force: bool = False,
) -> dict:
    """更新 pets.json（以宠物总览页物品栏为准）。"""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    os.makedirs(CATEGORIES_DIR, exist_ok=True)
    pets: dict[str, dict] = {} if force else _load_existing_pets()
    before = len(pets)

    catalog = load_pet_overview_catalog()
    if catalog:
        print(f"宠物总览页：{len(catalog)} 种召唤物品", flush=True)
        title_list = load_pet_summon_item_titles()
    else:
        titles = await fetch_category_members(session, PET_CATEGORY)
        print(f"{PET_CATEGORY}：{len(titles)} 个标题（未找到本地总览页）", flush=True)
        title_list = sorted(titles)

    new_count, images_ok, images_total = await ingest_pet_titles(
        session,
        pets,
        title_list,
        force=force,
    )
    with open(PETS_JSON, "w", encoding="utf-8") as f:
        json.dump(pets, f, ensure_ascii=False, indent=2)

    return {
        "before": before,
        "new_count": new_count,
        "total": len(pets),
        "images_ok": images_ok,
        "images_total": images_total,
    }


async def ingest_wiki_titles(
    session: aiohttp.ClientSession,
    items: dict[str, dict],
    title_list: list[str],
    *,
    refresh_sets: bool = False,
) -> tuple[int, int, int]:
    """抓取指定 Wiki 标题列表，返回 (新增数, 图片成功数, 图片总数)。"""
    existing_titles = _existing_wiki_titles(items)
    pending = [t for t in title_list if t not in existing_titles]
    pending.sort()
    image_urls: dict[str, str] = {}
    new_count = 0

    for i, title in enumerate(pending, 1):
        html = await fetch_page_html(session, title)
        if not html:
            continue
        item = parse_item_page(html, title)
        if not item:
            continue
        name = item["name"]
        if name in items:
            continue
        item["wiki_title"] = title
        _apply_overview_page_meta(item, title)
        _apply_search_aliases(item)
        items[name] = item
        if _is_set_item(item):
            _merge_set_pieces(items, name, item)
        image_urls.update(_collect_image_urls(item))
        new_count += 1
        if i % 25 == 0 or i == len(pending):
            print(
                f"抓取进度 {i}/{len(pending)}，新增 {new_count}，总计 {len(items)}",
                flush=True,
            )
        await asyncio.sleep(0.12)

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

    if refresh_sets:
        await refresh_armor_sets(session, items)

    return new_count, images_ok, len(image_urls)


async def ingest_new_categories(
    categories: list[str] | None = None,
    *,
    refresh_sets: bool = False,
) -> dict:
    """仅抓取指定分类中的新条目（默认：家具 + 其他）。"""
    cats = categories or ["Category:家具物品", "Category:其他物品"]
    items = _load_existing_items()
    before = len(items)

    connector = aiohttp.TCPConnector(limit=10)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        all_titles: set[str] = set()
        for cat in cats:
            titles = await fetch_category_members(session, cat)
            all_titles.update(titles)
            print(f"{cat}：{len(titles)} 个标题", flush=True)
            await asyncio.sleep(0.2)

        existing_titles = _existing_wiki_titles(items)
        new_titles = sorted(all_titles - existing_titles)
        print(f"待抓取新条目：{len(new_titles)}", flush=True)

        new_count, images_ok, images_total = await ingest_wiki_titles(
            session,
            items,
            new_titles,
            refresh_sets=refresh_sets,
        )

    migrate_item_image_filenames(items)
    strip_english_fields(items)
    piece_sync_count = resync_set_piece_locales(items)
    _persist_items(items)

    return {
        "before": before,
        "new_count": new_count,
        "total": len(items),
        "images_ok": images_ok,
        "images_total": images_total,
        "piece_sync_count": piece_sync_count,
    }


async def maintain_local_data() -> dict:
    """本地维护：规范化图片名、同步套装部件、清理 en 数据块。"""
    items = _load_existing_items()
    result = {
        "image_migrate_count": migrate_item_image_filenames(items),
        "piece_sync_count": resync_set_piece_locales(items),
        "en_stripped_count": strip_english_fields(items),
        "total": len(items),
        "sets_refreshed": 0,
    }
    _persist_items(items)
    return result


def _resolve_local_wiki_html(wiki_root: str, item: dict, key: str):
    from pathlib import Path

    for sub in ("zh/pages", "pages", "zh"):
        base = Path(wiki_root) / sub
        if not base.is_dir():
            continue
        for title in _iter_wiki_titles(item, key):
            path = base / f"{title}.html"
            if path.is_file():
                return path
    return None


def _description_should_refresh(item: dict, parsed: dict | None) -> bool:
    if not parsed:
        return False
    new_text = (parsed.get("text") or "").strip()
    old_text = (item.get("description") or "").strip()
    if not new_text or new_text == old_text:
        return False
    if _description_missing_intro_list(item):
        return True
    return len(new_text) > len(old_text)


def refresh_descriptions_from_local_wiki(
    items: dict[str, dict],
    *,
    wiki_root: str,
) -> tuple[int, list[str]]:
    """用本地 Wiki HTML 批量刷新物品描述（更长或补全截断列表）。"""
    updated_keys: list[str] = []
    for key, item in items.items():
        if item.get("from_wings_table"):
            continue
        path = _resolve_local_wiki_html(wiki_root, item, key)
        if not path:
            continue
        parsed = parse_description_from_soup(
            BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        )
        if not _description_should_refresh(item, parsed):
            continue
        if _apply_description_to_item(item, parsed):
            items[key] = item
            updated_keys.append(key)
    return len(updated_keys), updated_keys


async def refresh_sets_only() -> dict:
    """从 Wiki 重新抓取所有套装页并同步部件。"""
    items = _load_existing_items()
    migrate_item_image_filenames(items)
    connector = aiohttp.TCPConnector(limit=10)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        sets_refreshed = await refresh_armor_sets(session, items)
    piece_sync_count = resync_set_piece_locales(items)
    strip_english_fields(items)
    _persist_items(items)
    return {
        "sets_refreshed": sets_refreshed,
        "piece_sync_count": piece_sync_count,
        "total": len(items),
    }


async def main(
    limit: int | None = None,
    force: bool = False,
    desc_only: bool = False,
    desc_limit: int | None = None,
) -> None:
    if desc_only:
        existing = _load_existing_items()
        print(f"描述回填模式：已有 {len(existing)} 个物品", flush=True)
    elif force:
        print("全量模式：将重新抓取所有物品", flush=True)
    else:
        existing = _load_existing_items()
        print(f"增量模式：已有 {len(existing)} 个物品，跳过已存在的", flush=True)

    print("正在收集并更新 Wiki 数据...", flush=True)
    result = await update_wiki_data(
        limit=limit,
        force=force,
        desc_only=desc_only,
        desc_limit=desc_limit,
    )
    print(
        f"更新完成：新增 {result['new_count']} 个，"
        f"共 {result['total']} 个物品，"
        f"新图片 {result['images_ok']}/{result['images_total']}，"
        f"清理英文字段 {result.get('en_stripped_count', 0)} 个，"
        f"掉落来源 {result['drops_backfill_count']} 个，"
        f"描述 {result['desc_backfill_count']} 个",
        flush=True,
    )
    print(f"已保存到 {CATEGORIES_DIR}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="准备泰拉瑞亚 Wiki 离线数据")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 个页面（调试用）")
    parser.add_argument("--force", action="store_true", help="全量重建，覆盖已有数据")
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
    parser.add_argument(
        "--resync-pieces",
        action="store_true",
        help="本地维护：规范化图片名、同步套装部件、清理 en 数据块",
    )
    parser.add_argument(
        "--refresh-sets",
        action="store_true",
        help="从 Wiki 重新抓取所有套装页（盔甲/时装）",
    )
    parser.add_argument(
        "--strip-en",
        action="store_true",
        help="移除 categories 数据中的 en / en_name / aliases 字段",
    )
    parser.add_argument(
        "--ingest-categories",
        action="store_true",
        help="仅抓取新分类中的条目（家具/其他），跳过全量更新",
    )
    parser.add_argument(
        "--ingest-mounts",
        action="store_true",
        help="仅抓取坐骑召唤物到 mounts.json",
    )
    parser.add_argument(
        "--ingest-pets",
        action="store_true",
        help="仅抓取宠物召唤物到 categories/pets.json",
    )
    parser.add_argument(
        "--ingest-biomes",
        action="store_true",
        help="仅抓取生物群系到 categories/biomes.json",
    )
    parser.add_argument(
        "--ingest-npcs",
        action="store_true",
        help="仅抓取城镇 NPC 到 categories/npcs.json",
    )
    parser.add_argument(
        "--split-categories",
        action="store_true",
        help="将根目录 items/mounts/pets.json 拆分/迁移到 data/terraria_query/categories/",
    )
    parser.add_argument(
        "--remove-legacy",
        action="store_true",
        help="与 --split-categories 联用：迁移后删除根目录 items/mounts/pets.json",
    )
    args = parser.parse_args()
    try:
        if args.ingest_mounts:
            async def _run_mounts() -> dict:
                connector = aiohttp.TCPConnector(limit=10)
                timeout = aiohttp.ClientTimeout(total=60)
                async with aiohttp.ClientSession(
                    connector=connector, timeout=timeout
                ) as session:
                    return await refresh_mounts(session, force=args.force)

            result = asyncio.run(_run_mounts())
            print(
                f"坐骑抓取完成：新增 {result['new_count']} 个，"
                f"共 {result['total']} 条目，"
                f"图片 {result['images_ok']}/{result['images_total']}",
                flush=True,
            )
        elif args.ingest_pets:
            async def _run_pets() -> dict:
                connector = aiohttp.TCPConnector(limit=10)
                timeout = aiohttp.ClientTimeout(total=60)
                async with aiohttp.ClientSession(
                    connector=connector, timeout=timeout
                ) as session:
                    return await refresh_pets(session, force=args.force)

            result = asyncio.run(_run_pets())
            print(
                f"宠物抓取完成：新增 {result['new_count']} 个，"
                f"共 {result['total']} 条目，"
                f"图片 {result['images_ok']}/{result['images_total']}",
                flush=True,
            )
        elif args.ingest_biomes:
            async def _run_biomes() -> dict:
                connector = aiohttp.TCPConnector(limit=10)
                timeout = aiohttp.ClientTimeout(total=60)
                async with aiohttp.ClientSession(
                    connector=connector, timeout=timeout
                ) as session:
                    return await _biome_data_module().refresh_biomes(
                        session, force=args.force
                    )

            result = asyncio.run(_run_biomes())
            print(
                f"生物群系抓取完成：新增 {result['new_count']} 个，"
                f"共 {result['total']} 条目，"
                f"图片 {result['images_ok']}/{result['images_total']}",
                flush=True,
            )
        elif args.ingest_npcs:
            async def _run_npcs() -> dict:
                connector = aiohttp.TCPConnector(limit=10)
                timeout = aiohttp.ClientTimeout(total=60)
                async with aiohttp.ClientSession(
                    connector=connector, timeout=timeout
                ) as session:
                    return await _npc_data_module().refresh_npcs(
                        session, force=args.force
                    )

            result = asyncio.run(_run_npcs())
            print(
                f"NPC 抓取完成：新增 {result['new_count']} 个，"
                f"共 {result['total']} 条目，"
                f"图片 {result['images_ok']}/{result['images_total']}",
                flush=True,
            )
        elif args.ingest_categories:
            result = asyncio.run(ingest_new_categories())
            print(
                f"分类抓取完成：新增 {result['new_count']} 个，"
                f"共 {result['total']} 条目，"
                f"图片 {result['images_ok']}/{result['images_total']}",
                flush=True,
            )
        elif args.split_categories:
            cd = _category_data_module()
            result = asyncio.run(
                cd.migrate_to_categories_dir(remove_legacy=args.remove_legacy)
            )
            print(cd.format_split_report(result), flush=True)
            print(f"已写入 {result['categories_dir']}", flush=True)
            if args.remove_legacy:
                print("已删除根目录 items/mounts/pets.json", flush=True)
        elif args.resync_pieces:
            result = asyncio.run(maintain_local_data())
            print(
                f"本地维护完成：图片名修正 {result['image_migrate_count']} 处，"
                f"部件同步 {result['piece_sync_count']} 个，共 {result['total']} 条目",
                flush=True,
            )
        elif args.refresh_sets:
            result = asyncio.run(refresh_sets_only())
            print(
                f"套装刷新完成：{result['sets_refreshed']} 套，"
                f"部件同步 {result['piece_sync_count']} 个，共 {result['total']} 条目",
                flush=True,
            )
        elif args.strip_en:
            items = _load_existing_items()
            count = strip_english_fields(items)
            _persist_items(items)
            print(f"已清理 {count} 个 en 数据块，共 {len(items)} 条目", flush=True)
        else:
            asyncio.run(
                main(
                    limit=args.limit,
                    force=args.force,
                    desc_only=args.desc_only,
                    desc_limit=args.desc_limit,
                )
            )
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(1)
