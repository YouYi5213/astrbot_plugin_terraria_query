import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from treasure_bag_data import (  # noqa: E402
    build_treasure_bags_from_mirror,
    parse_treasure_bags_file,
)


def test_parse_treasure_bag_catalog_count():
    bags = parse_treasure_bags_file()
    assert len(bags) == 18
    names = {bag["name"] for bag in bags}
    assert "史莱姆王" in names
    assert "月亮领主" in names
    assert "拜月教邪教徒" not in names


def test_king_slime_treasure_bag_drops():
    bags = build_treasure_bags_from_mirror()
    bag = bags["史莱姆王"]
    assert bag["page_type"] == "treasure_bag"
    assert bag.get("item_id") == "3318"
    assert len(bag["drops"]) >= 10
    expert_rows = [d for d in bag["drops"] if d.get("expert_exclusive")]
    assert len(expert_rows) == 1
    assert expert_rows[0]["name"] == "皇家凝胶"
    assert expert_rows[0]["chance"] == "100%"


def test_moon_lord_treasure_bag_expert_rows():
    bags = build_treasure_bags_from_mirror()
    bag = bags["月亮领主"]
    expert_rows = [d for d in bag["drops"] if d.get("expert_exclusive")]
    assert len(expert_rows) == 3


def test_treasure_bag_search_terms_exclude_bare_boss_name():
    bags = build_treasure_bags_from_mirror()
    bag = bags["月亮领主"]
    terms = set(bag.get("search_terms") or [])
    assert "月亮领主" not in terms
    assert "月亮领主宝藏袋" in terms
    assert "月总宝藏袋" in terms
