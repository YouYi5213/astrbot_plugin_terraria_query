import importlib.util
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 构造插件包，使 main.py 的相对导入可用
pkg = types.ModuleType("astrbot_plugin_terraria_query")
pkg.__path__ = [str(ROOT)]
sys.modules["astrbot_plugin_terraria_query"] = pkg

prep_spec = importlib.util.spec_from_file_location(
    "astrbot_plugin_terraria_query.prepare_data",
    ROOT / "prepare_data.py",
)
prep = importlib.util.module_from_spec(prep_spec)
assert prep_spec and prep_spec.loader
sys.modules["astrbot_plugin_terraria_query.prepare_data"] = prep
prep_spec.loader.exec_module(prep)

for mod_name in ("astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star"):
    sys.modules.setdefault(mod_name, types.ModuleType(mod_name))

filter_stub = types.SimpleNamespace(
    regex=lambda *args, **kwargs: (lambda fn: fn),
    on_astrbot_loaded=lambda *args, **kwargs: (lambda fn: fn),
)
sys.modules["astrbot.api.event"].filter = filter_stub
sys.modules["astrbot.api.event"].AstrMessageEvent = object
sys.modules["astrbot.api.star"].Context = object
sys.modules["astrbot.api.star"].Star = object
sys.modules["astrbot.api"].AstrBotConfig = dict
sys.modules["astrbot.api"].logger = types.SimpleNamespace(
    info=print, warning=print, error=print
)

main_spec = importlib.util.spec_from_file_location(
    "astrbot_plugin_terraria_query.main",
    ROOT / "main.py",
    submodule_search_locations=[str(ROOT)],
)
main = importlib.util.module_from_spec(main_spec)
assert main_spec and main_spec.loader
sys.modules["astrbot_plugin_terraria_query.main"] = main
main_spec.loader.exec_module(main)


def test_resolve_inline_icon_path_fallback(tmp_path, monkeypatch):
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    (img_dir / "Titanium_Mask.png").write_bytes(b"fake")
    monkeypatch.setattr(main, "IMAGES_DIR", str(img_dir))
    path = main._resolve_inline_icon_path("17px-Titanium_Mask.png")
    assert path.endswith("Titanium_Mask.png")


def test_format_text_includes_set_pieces():
    data = {
        "name": "钛金盔甲",
        "stats": [{"label": "类型", "value": "盔甲套装"}],
        "set_pieces": [
            {
                "name": "钛金面具",
                "stats": [{"label": "防御", "value": "24"}],
                "recipe": {
                    "station": "秘银砧",
                    "ingredients": [{"name": "钛金锭", "amount": "13"}],
                    "result": {"name": "钛金面具"},
                },
            }
        ],
    }
    text = main._format_text_result(data)
    assert "套装部件" in text
    assert "钛金面具" in text
    assert "秘银砧" in text


def test_load_inline_icon_helper_exists():
    assert callable(main._load_inline_icon)
    assert callable(main._load_item_image)


def test_teraria_cmd_regex():
    pattern = re.compile(main._TERRARIA_CMD_RE, re.I)
    assert pattern.match("泰拉 天顶剑")
    assert pattern.match("泰拉强制更新")
    assert pattern.match("/泰拉查询 剑")


def test_fuzzy_match_english_name_shows_chinese_item():
    items = {
        "环境改造枪": {
            "name": "环境改造枪",
            "en_name": "Clentaminator",
            "stats": [],
        }
    }
    matches = main._fuzzy_match("Clentaminator", items)
    assert matches == ["环境改造枪"]
    display = main._display_item(items["环境改造枪"])
    assert display["name"] == "环境改造枪"


def test_fuzzy_match_all_includes_mounts():
    items = {"天顶剑": {"name": "天顶剑", "stats": []}}
    mounts = {
        "虾松露": {
            "name": "虾松露",
            "en_name": "Shrimpy Truffle",
            "page_type": "mount",
            "stats": [],
            "buff": {"name": "可爱猪龙鱼坐骑"},
            "mount": {"name": "可爱猪龙鱼坐骑"},
            "search_terms": ["可爱猪龙鱼"],
        }
    }
    matches = main._fuzzy_match_all("Shrimpy Truffle", items, mounts)
    assert matches == [("mount", "虾松露")]
    matches_zh = main._fuzzy_match_all("虾松露", items, mounts)
    assert matches_zh == [("mount", "虾松露")]
    matches_mount = main._fuzzy_match_all("可爱猪龙鱼", items, mounts)
    assert matches_mount == [("mount", "虾松露")]


def test_fuzzy_match_all_includes_pets():
    items = {"天顶剑": {"name": "天顶剑", "stats": []}}
    mounts: dict = {}
    pets = {
        "蚊子琥珀": {
            "name": "蚊子琥珀",
            "en_name": "Mosquito in Amber",
            "page_type": "pet",
            "stats": [],
            "buff": {"name": "恐龙宝宝"},
            "pet": {"name": "恐龙宝宝"},
            "search_terms": ["恐龙宝宝"],
        }
    }
    matches = main._fuzzy_match_all("Mosquito in Amber", items, mounts, pets)
    assert matches == [("pet", "蚊子琥珀")]
    matches_pet = main._fuzzy_match_all("恐龙宝宝", items, mounts, pets)
    assert matches_pet == [("pet", "蚊子琥珀")]
