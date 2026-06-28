import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from npc_data import (  # noqa: E402
    build_npcs_from_mirror,
    load_npc_catalog_from_overview,
    parse_npc_page_file,
)


def test_load_npc_catalog_has_town_npcs():
    catalog = load_npc_catalog_from_overview()
    assert len(catalog) >= 26
    titles = {entry["wiki_title"] for entry in catalog}
    assert "军火商" in titles
    assert "向导" in titles
    assert "旅商" in titles


def test_parse_arms_dealer_npc():
    catalog = {e["wiki_title"]: e for e in load_npc_catalog_from_overview()}
    parsed = parse_npc_page_file("军火商", catalog_entry=catalog["军火商"])
    assert parsed is not None
    assert parsed["name"] == "军火商"
    assert parsed["page_type"] == "npc"
    assert parsed["image"] == "Arms_Dealer.png"
    assert "portrait" not in parsed["image"].lower()
    assert "枪" in parsed["description"]
    assert "房屋" in parsed["spawn"]
    assert parsed.get("shop")
    musket = next(item for item in parsed["shop"] if item["name"] == "火枪子弹")
    assert musket.get("image") == "Musket_Ball.png"
    assert musket.get("coins")
    assert parsed.get("preferences")
    assert isinstance(parsed["preferences"][0]["neighbors"][0], dict)
    assert parsed.get("shimmer")
    assert parsed.get("shimmer_image")


def test_parse_guide_npc_no_shop():
    catalog = {e["wiki_title"]: e for e in load_npc_catalog_from_overview()}
    parsed = parse_npc_page_file("向导", catalog_entry=catalog["向导"])
    assert parsed is not None
    assert parsed.get("description")
    assert not parsed.get("shop")


def test_build_npcs_from_mirror():
    npcs = build_npcs_from_mirror()
    assert len(npcs) >= 26
    assert "军火商" in npcs
