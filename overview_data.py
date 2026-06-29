"""分类总览卡片：从 Wiki 目录构建分组条目。"""

from __future__ import annotations

from typing import Any

try:
    from .biome_data import load_biome_catalog_from_homepage
    from .boss_data import load_boss_catalog_from_homepage
    from .event_data import load_event_catalog_from_homepage
    from .npc_data import load_npc_catalog_from_homepage
except ImportError:
    from biome_data import load_biome_catalog_from_homepage
    from boss_data import load_boss_catalog_from_homepage
    from event_data import load_event_catalog_from_homepage
    from npc_data import load_npc_catalog_from_homepage

OverviewSection = dict[str, Any]
OverviewLayout = dict[str, Any]

_SECTION_LABELS = {
    "pre_hardmode": "困难模式之前",
    "hardmode": "困难模式",
}


def _pool_lookup(pool: dict[str, dict], wiki_title: str) -> dict:
    if wiki_title in pool:
        return pool[wiki_title]
    for item in pool.values():
        if item.get("wiki_title") == wiki_title or item.get("name") == wiki_title:
            return item
    return {}


def _item_from_catalog(entry: dict[str, str], pool: dict[str, dict]) -> dict[str, str]:
    wiki_title = entry.get("wiki_title") or entry.get("label") or ""
    pooled = _pool_lookup(pool, wiki_title)
    name = pooled.get("name") or entry.get("label") or wiki_title
    image = entry.get("image") or pooled.get("list_icon") or ""
    return {"name": name, "image": image}


def _entry_category(entry: dict[str, str]) -> str:
    category = entry.get("category") or entry.get("phase") or ""
    if category == "other":
        return ""
    return category


def _catalog_from_pool(pool: dict[str, dict]) -> list[dict[str, str]]:
    return [
        {
            "wiki_title": value.get("wiki_title") or key,
            "label": value.get("name", key),
            "category": _entry_category(value),
            "image": value.get("list_icon", ""),
        }
        for key, value in pool.items()
    ]


def _split_pool_fallback_sections(pool: dict[str, dict]) -> list[OverviewSection]:
    items = sorted(
        [
            {
                "name": value.get("name", key),
                "image": value.get("list_icon", ""),
            }
            for key, value in pool.items()
        ],
        key=lambda row: row["name"],
    )
    if not items:
        return []
    mid = (len(items) + 1) // 2
    return [
        {"label": _SECTION_LABELS["pre_hardmode"], "items": items[:mid]},
        {"label": _SECTION_LABELS["hardmode"], "items": items[mid:]},
    ]


def _sections_from_catalog(
    catalog: list[dict[str, str]],
    pool: dict[str, dict],
    *,
    categories: tuple[str, ...],
) -> list[OverviewSection]:
    sections: list[OverviewSection] = []
    for category in categories:
        items = [
            _item_from_catalog(entry, pool)
            for entry in catalog
            if _entry_category(entry) == category
        ]
        if items:
            sections.append(
                {
                    "label": _SECTION_LABELS.get(category, category),
                    "items": items,
                }
            )
    return sections


def _build_grouped_overview(
    pool: dict[str, dict],
    *,
    title: str,
    load_catalog,
    categories: tuple[str, ...] = ("pre_hardmode", "hardmode"),
) -> OverviewLayout:
    catalog = load_catalog()
    if not catalog:
        catalog = _catalog_from_pool(pool)
    sections = _sections_from_catalog(catalog, pool, categories=categories)
    if not sections and pool:
        sections = _split_pool_fallback_sections(pool)
    return {"title": title, "layout": "columns", "sections": sections}


def build_boss_overview(bosses: dict[str, dict]) -> OverviewLayout:
    pool = {
        key: value
        for key, value in bosses.items()
        if not value.get("exclude_overview")
    }
    return _build_grouped_overview(
        pool,
        title="Boss",
        load_catalog=load_boss_catalog_from_homepage,
    )


def build_event_overview(events: dict[str, dict]) -> OverviewLayout:
    return _build_grouped_overview(
        events,
        title="事件",
        load_catalog=load_event_catalog_from_homepage,
    )


def build_npc_overview(npcs: dict[str, dict]) -> OverviewLayout:
    return _build_grouped_overview(
        npcs,
        title="NPC",
        load_catalog=load_npc_catalog_from_homepage,
    )


def build_biome_overview(biomes: dict[str, dict]) -> OverviewLayout:
    catalog = load_biome_catalog_from_homepage()
    if not catalog:
        catalog = [
            {
                "wiki_title": key,
                "label": value.get("name", key),
                "image": value.get("list_icon", ""),
            }
            for key, value in biomes.items()
        ]
    items = [_item_from_catalog(entry, biomes) for entry in catalog]
    return {
        "title": "生物群系",
        "layout": "grid",
        "columns": 3,
        "sections": [{"label": "", "items": items}],
    }


def build_treasure_bag_overview(treasure_bags: dict[str, dict]) -> OverviewLayout:
    items = []
    for key in sorted(treasure_bags.keys()):
        bag = treasure_bags[key]
        name = bag.get("name") or key
        items.append(
            {
                "name": f"{name}宝藏袋",
                "image": bag.get("image", ""),
            }
        )
    return {
        "title": "宝藏袋",
        "layout": "grid",
        "columns": 2,
        "sections": [{"label": "", "items": items}],
    }
