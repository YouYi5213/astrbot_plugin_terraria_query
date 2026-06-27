import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prepare_data import (  # noqa: E402
    _is_set_item,
    _merge_set_pieces,
    _needs_en_locale_refresh,
    _normalize_image_filename,
    migrate_item_image_filenames,
    resync_set_piece_locales,
)


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


def test_merge_set_pieces_syncs_en_by_index():
    items: dict = {}
    set_item = {
        "set_pieces": [
            {"name": "钛金面具", "image": "Titanium_Mask.png", "stats": [], "recipe": None},
            {"name": "钛金头盔", "image": "Titanium_Helmet.png", "stats": [], "recipe": None},
        ],
        "en": {
            "set_pieces": [
                {"name": "Titanium Mask", "image": "Titanium_Mask.png", "stats": [], "recipe": None},
                {"name": "Titanium Helmet", "image": "Titanium_Helmet.png", "stats": [], "recipe": None},
            ]
        },
    }
    count = _merge_set_pieces(items, "钛金盔甲", set_item)
    assert count == 2
    assert items["钛金面具"]["en"]["name"] == "Titanium Mask"
    assert items["钛金面具"]["en_name"] == "Titanium Mask"


def test_resync_set_piece_locales():
    items = {
        "钛金盔甲": {
            "page_type": "armor_set",
            "set_pieces": [
                {"name": "寒霜头盔", "image": "Frost_Helmet.png", "stats": [], "recipe": None},
            ],
            "en": {
                "set_pieces": [
                    {"name": "Frost Helmet", "image": "Frost_Helmet.png", "stats": [], "recipe": None},
                ]
            },
        }
    }
    updated = resync_set_piece_locales(items)
    assert updated == 1
    assert items["寒霜头盔"]["en_name"] == "Frost Helmet"


def test_migrate_item_image_filenames():
    items = {
        "测试": {
            "image": "30px-Amethyst_Staff.png",
            "stats": [
                {
                    "label": "防御",
                    "segments": [{"type": "icon", "image": "17px-Titanium_Mask.png"}],
                }
            ],
        }
    }
    changed = migrate_item_image_filenames(items)
    assert changed >= 2
    assert items["测试"]["image"] == "Amethyst_Staff.png"
    assert items["测试"]["stats"][0]["segments"][0]["image"] == "Titanium_Mask.png"


def test_needs_en_locale_refresh_when_set_pieces_missing():
    item = {
        "en": {"name": "Titanium armor", "set_pieces": []},
        "set_pieces": [{"name": "钛金面具"}],
    }
    assert _needs_en_locale_refresh(item)
    item["en"]["set_pieces"] = [{"name": "Titanium Mask"}]
    assert not _needs_en_locale_refresh(item)


def test_items_json_is_valid():
    items_path = ROOT / "data" / "terraria_query" / "items.json"
    with open(items_path, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert len(data) > 1000
