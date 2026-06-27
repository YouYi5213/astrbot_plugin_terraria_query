import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prepare_data import (  # noqa: E402
    _description_is_tooltip_only,
    _description_needs_coin_refresh,
    _description_needs_zh_refresh,
    _is_set_item,
    _merge_set_pieces,
    _normalize_image_filename,
    _parse_description_paragraph_rich,
    migrate_item_image_filenames,
    resync_set_piece_locales,
    strip_en_locale_data,
)
from bs4 import BeautifulSoup  # noqa: E402


def test_normalize_image_filename_strips_px_prefix():
    assert _normalize_image_filename("17px-Titanium_Mask.png") == "Titanium_Mask.png"
    assert _normalize_image_filename("Amethyst_Staff.png") == "Amethyst_Staff.png"


def test_is_set_item_detects_armor_and_vanity():
    assert _is_set_item({"stats": [{"label": "类型", "value": "盔甲套装"}]})
    assert _is_set_item({"stats": [{"label": "Type", "value": "Vanity set"}]})
    assert _is_set_item({"stats": [{"label": "Type", "value": "ArmorSet"}]})
    assert _is_set_item({"stats": [{"label": "Type", "value": "VanitySet"}]})
    assert _is_set_item({"stats": [{"label": "类型", "value": "时装套装"}]})
    assert not _is_set_item({"stats": [{"label": "类型", "value": "武器"}]})


def test_merge_set_pieces_uses_image_for_en_name():
    items: dict = {}
    set_item = {
        "set_pieces": [
            {"name": "钛金面具", "image": "Titanium_Mask.png", "stats": [], "recipe": None},
        ],
    }
    count = _merge_set_pieces(items, "钛金盔甲", set_item)
    assert count == 1
    assert items["钛金面具"]["en_name"] == "Titanium Mask"
    assert "en" not in items["钛金面具"]


def test_resync_set_piece_locales():
    items = {
        "钛金盔甲": {
            "page_type": "armor_set",
            "set_pieces": [
                {"name": "寒霜头盔", "image": "Frost_Helmet.png", "stats": [], "recipe": None},
            ],
        }
    }
    updated = resync_set_piece_locales(items)
    assert updated == 1
    assert items["寒霜头盔"]["en_name"] == "Frost Helmet"


def test_strip_en_locale_data_keeps_en_name():
    items = {
        "环境改造枪": {
            "name": "环境改造枪",
            "en": {"name": "Clentaminator", "stats": []},
        }
    }
    assert strip_en_locale_data(items) == 1
    assert items["环境改造枪"]["en_name"] == "Clentaminator"
    assert "en" not in items["环境改造枪"]


def test_migrate_item_image_filenames():
    items = {
        "测试": {
            "image": "30px-Amethyst_Staff.png",
            "stats": [
                {
                    "label": "防御",
                    "value": "10",
                    "value_image": "17px-Titanium_Mask.png",
                }
            ],
        }
    }
    assert migrate_item_image_filenames(items) == 2
    assert items["测试"]["image"] == "Amethyst_Staff.png"
    assert items["测试"]["stats"][0]["value_image"] == "Titanium_Mask.png"


def test_parse_description_coin_segment():
    html = (
        '<p>可以从松露人处以<span class="coin">'
        '<span class="pc">1<i> PC</i></span></span>购买它。</p>'
    )
    p = BeautifulSoup(html, "lxml").p
    segments = _parse_description_paragraph_rich(p)
    assert any(s.get("type") == "coin" and s.get("coin_type") == "pc" for s in segments)


def test_description_needs_coin_refresh():
    item = {
        "description": "可以从松露人处以1购买它。",
        "description_rich": [[{"type": "text", "text": "可以从松露人处以1购买它。"}]],
    }
    assert _description_needs_coin_refresh(item)
    item["description_rich"] = [[{"type": "coin", "amount": "1", "coin_type": "pc"}]]
    assert not _description_needs_coin_refresh(item)


def test_description_needs_zh_refresh_detects_tooltip_only():
    item = {
        "name": "环境改造枪",
        "description": "喷射时生成和摧毁生物群系使用彩色溶液",
        "stats": [{"label": "工具提示", "value": "喷射时生成和摧毁生物群系使用彩色溶液"}],
    }
    assert _description_is_tooltip_only(item)
    assert _description_needs_zh_refresh(item)


def test_description_needs_zh_refresh_detects_english_on_chinese_item():
    item = {
        "name": "环境改造枪",
        "description": "The Clentaminator is a Hardmode tool.",
    }
    assert _description_needs_zh_refresh(item)
    item["description"] = "环境改造枪是一种困难模式工具。"
    assert not _description_needs_zh_refresh(item)


def test_items_json_is_valid():
    items_path = ROOT / "data" / "terraria_query" / "items.json"
    with open(items_path, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert len(data) > 1000
