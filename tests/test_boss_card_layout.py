import sys
import types


class _FilterMock:
    def __getattr__(self, name):
        def deco(*a, **k):
            def wrap(f):
                return f

            return wrap

        return deco


def _bootstrap_plugin_package():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    pkg = types.ModuleType("astrbot_plugin_terraria_query")
    pkg.__path__ = [str(root)]
    sys.modules["astrbot_plugin_terraria_query"] = pkg
    for mod in ["astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star"]:
        sys.modules.setdefault(mod, types.ModuleType(mod))
    sys.modules["astrbot.api.event"].filter = _FilterMock()
    sys.modules["astrbot.api.event"].AstrMessageEvent = object
    sys.modules["astrbot.api.star"].Context = object
    sys.modules["astrbot.api.star"].Star = object
    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = object
    api.logger = type("L", (), {"info": print, "warning": print, "error": print})()
    sys.modules["astrbot.api"] = api


def _import_main():
    _bootstrap_plugin_package()
    from astrbot_plugin_terraria_query.main import _compact_boss_stat_multiline

    return _compact_boss_stat_multiline


def test_compact_boss_damage_merges_number_and_note():
    compact = _import_main()
    raw = (
        "0 （接触）\n450\n（ 幻影死亡射线 ）\n180\n（ 幻影矢 ）\n"
        "420\n（ For the Worthy 和 Get fixed boi 世界中的 月亮巨石 ）"
    )
    out = compact(raw, "伤害")
    lines = out.split("\n")
    assert len(lines) == 4
    assert lines[0].startswith("· 0 （接触）")
    assert "450" in lines[1] and "幻影死亡射线" in lines[1]
    assert "420" in lines[3] and "月亮巨石" in lines[3]


def test_compact_boss_stat_short_value_unchanged():
    compact = _import_main()
    assert compact("45000", "最大生命值") == "45000"
    assert compact("100%", "击退抗性") == "100%"
