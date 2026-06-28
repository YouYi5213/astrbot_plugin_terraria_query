import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from biome_data import parse_biome_page_file  # noqa: E402
from event_data import parse_event_page_file  # noqa: E402
from page_section_data import (  # noqa: E402
    parse_conditions_section,
    parse_content_section,
)


def _load_soup(wiki_title: str):
    from bs4 import BeautifulSoup

    from event_data import _event_page_html_path

    html = Path(_event_page_html_path(wiki_title)).read_text(encoding="utf-8")
    return BeautifulSoup(html, "html.parser")


def test_blood_moon_has_conditions_and_content():
    parsed = parse_event_page_file("血月")
    assert parsed is not None
    assert parsed.get("conditions")
    assert "120" in parsed["conditions"]
    assert parsed.get("conditions_rich")
    content = parsed.get("content")
    assert content
    headings = [group.get("heading") for group in content]
    assert "角色" in headings
    assert "独特掉落" in headings
    role_group = next(g for g in content if g.get("heading") == "角色")
    assert role_group["boxes"]
    assert any("任何时间" in box.get("title", "") for box in role_group["boxes"])
    names = {
        item.get("name")
        for box in role_group["boxes"]
        for item in box.get("items") or []
    }
    assert "僵尸新郎" in names
    groom = next(
        item
        for box in role_group["boxes"]
        for item in box.get("items") or []
        if item.get("name") == "僵尸新郎"
    )
    assert groom.get("image") == "The_Groom.png"


def test_forest_biome_has_content_without_conditions():
    parsed = parse_biome_page_file("森林")
    assert parsed is not None
    assert not parsed.get("conditions")
    content = parsed.get("content")
    assert content
    headings = [group.get("heading") for group in content]
    assert "角色" in headings
    assert "独特掉落" in headings


def test_parse_conditions_section_direct():
    soup = _load_soup("血月")
    conditions = parse_conditions_section(soup)
    assert conditions is not None
    assert "血泪" in conditions["text"]


def test_parse_content_section_direct():
    soup = _load_soup("血月")
    content = parse_content_section(soup)
    assert content is not None
    assert content[0]["boxes"]
