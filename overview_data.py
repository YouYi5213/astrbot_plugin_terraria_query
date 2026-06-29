"""分类总览卡片：从 Wiki 目录构建分组条目。"""

from __future__ import annotations

from typing import Any

try:
    from .biome_data import load_biome_catalog_from_homepage
    from .boss_data import load_boss_catalog_from_overview
    from .event_data import load_event_catalog_from_homepage
    from .npc_data import load_npc_catalog_from_homepage
except ImportError:
    from biome_data import load_biome_catalog_from_homepage
    from boss_data import load_boss_catalog_from_overview
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
    image = entry.get("image") or pooled.get("image") or ""
    return {"name": name, "image": image}


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
            if entry.get("category") == category
        ]
        if items:
            sections.append(
                {
                    "label": _SECTION_LABELS.get(category, category),
                    "items": items,
                }
            )
    return sections


def build_boss_overview(bosses: dict[str, dict]) -> OverviewLayout:
    catalog = load_boss_catalog_from_overview()
    if not catalog:
        catalog = [
            {
                "wiki_title": key,
                "label": value.get("name", key),
                "category": value.get("category", ""),
                "image": value.get("image", ""),
            }
            for key, value in bosses.items()
        ]
    sections = _sections_from_catalog(
        catalog,
        bosses,
        categories=("pre_hardmode", "hardmode"),
    )
    return {"title": "Boss", "layout": "columns", "sections": sections}


def build_event_overview(events: dict[str, dict]) -> OverviewLayout:
    catalog = load_event_catalog_from_homepage()
    if not catalog:
        catalog = [
            {
                "wiki_title": key,
                "label": value.get("name", key),
                "category": "",
                "image": value.get("image", ""),
            }
            for key, value in events.items()
        ]
    sections = _sections_from_catalog(
        catalog,
        events,
        categories=("pre_hardmode", "hardmode"),
    )
    return {"title": "事件", "layout": "columns", "sections": sections}


def build_npc_overview(npcs: dict[str, dict]) -> OverviewLayout:
    catalog = load_npc_catalog_from_homepage()
    if not catalog:
        catalog = [
            {
                "wiki_title": key,
                "label": value.get("name", key),
                "category": value.get("category", ""),
                "image": value.get("image", ""),
            }
            for key, value in npcs.items()
        ]
    sections = _sections_from_catalog(
        catalog,
        npcs,
        categories=("pre_hardmode", "hardmode"),
    )
    return {"title": "NPC", "layout": "columns", "sections": sections}


def build_biome_overview(biomes: dict[str, dict]) -> OverviewLayout:
    catalog = load_biome_catalog_from_homepage()
    if not catalog:
        catalog = [
            {
                "wiki_title": key,
                "label": value.get("name", key),
                "image": value.get("image", ""),
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
