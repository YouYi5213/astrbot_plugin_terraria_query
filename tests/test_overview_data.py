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


def test_build_treasure_bag_overview():
    overview = build_treasure_bag_overview(
        {
            "史莱姆王": {"name": "史莱姆王", "image": "King_Slime_Treasure_Bag.png"},
        }
    )
    assert overview["layout"] == "grid"
    assert overview["sections"][0]["items"][0]["name"] == "史莱姆王宝藏袋"
