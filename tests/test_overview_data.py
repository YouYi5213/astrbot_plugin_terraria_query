import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from overview_data import (  # noqa: E402
    build_biome_overview,
    build_boss_overview,
    build_event_overview,
    build_npc_overview,
    build_treasure_bag_overview,
)


def test_build_boss_overview_has_two_sections():
    overview = build_boss_overview({})
    assert overview["layout"] == "columns"
    assert len(overview["sections"]) == 2
    assert overview["sections"][0]["label"] == "困难模式之前"
    assert overview["sections"][1]["label"] == "困难模式"
    assert len(overview["sections"][0]["items"]) >= 8
    assert len(overview["sections"][1]["items"]) >= 8


def test_build_event_overview_has_pre_and_hardmode():
    overview = build_event_overview({})
    labels = [section["label"] for section in overview["sections"]]
    assert "困难模式之前" in labels
    assert "困难模式" in labels
    total = sum(len(section["items"]) for section in overview["sections"])
    assert total >= 14


def test_build_npc_overview_from_homepage():
    overview = build_npc_overview({})
    assert overview["title"] == "NPC"
    total = sum(len(section["items"]) for section in overview["sections"])
    assert total >= 20


def test_build_biome_overview_grid():
    overview = build_biome_overview({})
    assert overview["layout"] == "grid"
    assert overview["columns"] == 3
    assert len(overview["sections"][0]["items"]) >= 20


def test_build_boss_overview_uses_homepage_icons():
    overview = build_boss_overview({})
    pre_items = overview["sections"][0]["items"]
    assert pre_items
    assert pre_items[0]["image"].startswith("Map_Icon_")


def test_build_event_overview_uses_homepage_icons():
    overview = build_event_overview({})
    items = overview["sections"][0]["items"]
    assert items
    assert items[0]["image"].startswith(("Bestiary_", "Torch_"))


def test_build_biome_overview_uses_homepage_icons():
    overview = build_biome_overview({})
    items = overview["sections"][0]["items"]
    assert items
    assert items[0]["image"].startswith("Bestiary_")


def test_build_event_overview_fallback_without_catalog(monkeypatch):
    monkeypatch.setattr(
        "overview_data.load_event_catalog_from_homepage",
        lambda: [],
    )
    overview = build_event_overview(
        {
            "血月": {"name": "血月", "list_icon": "Bestiary_Blood_Moon.png"},
            "日食": {"name": "日食", "list_icon": "Bestiary_Eclipse.png"},
        }
    )
    total = sum(len(section["items"]) for section in overview["sections"])
    assert total == 2
    images = {
        item["image"]
        for section in overview["sections"]
        for item in section["items"]
    }
    assert "BiomeBannerBloodMoon.png" not in images


def test_build_npc_overview_uses_phase_without_homepage(monkeypatch):
    monkeypatch.setattr(
        "overview_data.load_npc_catalog_from_homepage",
        lambda: [],
    )
    overview = build_npc_overview(
        {
            "向导": {"name": "向导", "phase": "pre_hardmode", "list_icon": "Map_Icon_Guide.png"},
            "巫师": {"name": "巫师", "phase": "hardmode", "list_icon": "Map_Icon_Wizard.png"},
        }
    )
    labels = {section["label"]: len(section["items"]) for section in overview["sections"]}
    assert labels.get("困难模式之前") == 1
    assert labels.get("困难模式") == 1

    overview = build_treasure_bag_overview(
        {
            "史莱姆王": {"name": "史莱姆王", "image": "King_Slime_Treasure_Bag.png"},
        }
    )
    assert overview["layout"] == "grid"
    assert overview["sections"][0]["items"][0]["name"] == "史莱姆王宝藏袋"
