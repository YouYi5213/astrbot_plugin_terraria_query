import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from event_data import (  # noqa: E402
    EVENT_SEARCH_ALIASES,
    build_events_from_mirror,
    load_event_catalog_from_homepage,
    parse_event_page_file,
)


def test_load_event_catalog_has_16_entries():
    catalog = load_event_catalog_from_homepage()
    assert len(catalog) == 16
    titles = {entry["wiki_title"] for entry in catalog}
    assert "血月" in titles
    assert "哥布林军队" in titles


def test_parse_blood_moon_event_page():
    parsed = parse_event_page_file("血月")
    assert parsed is not None
    assert parsed["name"] == "血月"
    assert parsed["image"] == "BiomeBannerBloodMoon.png"
    assert parsed["page_type"] == "event"
    assert parsed["description"]
    assert parsed["description"].startswith("天空变红的时候，你就知道血月升起来了。")
    assert "向导" in parsed["description"]
    assert "血月正在升起……" in parsed["description"]
    assert "血月是一个事件" in parsed["description"]
    assert parsed.get("conditions")
    assert "120" in parsed["conditions"]
    assert parsed.get("content")
    assert any(g.get("heading") == "角色" for g in parsed["content"])


def test_moon_event_alias():
    parsed = parse_event_page_file("月亮事件")
    assert parsed is not None
    assert "月柱" in parsed.get("search_terms", [])


def test_build_events_from_mirror():
    events = build_events_from_mirror()
    assert len(events) == 16
    for wiki_title in EVENT_SEARCH_ALIASES:
        key = next(
            (k for k, v in events.items() if v.get("wiki_title") == wiki_title),
            None,
        )
        assert key is not None, wiki_title
