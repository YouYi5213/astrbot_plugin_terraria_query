# astrbot_plugin_terraria_query

泰拉瑞亚 Wiki 离线查询 AstrBot 插件。发送 `泰拉查询 <物品名>` 或 `泰拉 <物品名>`（无需 `/` 前缀），以图片卡片展示物品属性与合成配方，支持中英文模糊匹配。

## 功能

- **物品查询** — `泰拉查询 天顶剑` / `泰拉 Zenith` / `泰拉 天顶剑`
- **中文模糊匹配** — 如「天顶」→「天顶剑」
- **图片卡片** — 属性、描述、合成配方、掉落来源；失败时降级为文本
- **盔甲/时装套装** — 分部件展示属性与配方（如寒霜盔甲、兔兔套装）
- **套装部件** — 可单独查询（如 `泰拉 钛金面具` / `泰拉 Titanium Mask`）
- **翅膀** — 支持总览页与按名称查询（如猪龙鱼之翼）
- **双语** — 中文/英文 Wiki 数据，英文查询展示英文卡片

## 安装

1. 将本仓库克隆到 AstrBot 的 `data/plugins/` 目录，或通过 WebUI 从 GitHub Release 安装
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 插件已自带 `data/terraria_query/` 离线数据，安装后可直接使用
4. 在 AstrBot **WebUI → 插件 → 泰拉瑞亚查询** 中配置（可选）：
   - **定时更新 Wiki 数据**：Cron 表达式，如 `0 4 * * 0`
   - **更新指令管理员 ID**：限制谁可以执行更新指令
5. 在 WebUI 重载插件

## 指令

| 指令 | 说明 |
|------|------|
| `泰拉查询 <物品名>` / `泰拉 <物品名>` | 查询物品，支持中英文模糊匹配 |
| `泰拉更新` | 从 Wiki **增量**更新（新增物品、刷新套装、回填英文等） |
| `泰拉强制更新` | **全量重建**（管理员，耗时较长） |

> 群聊中无需 `/` 前缀；`/泰拉查询` 等写法仍然有效。

## 使用示例

```
泰拉 天顶剑
泰拉 Zenith
泰拉 钛金盔甲
泰拉 Titanium Mask
泰拉 寒霜头盔
泰拉 翅膀
泰拉更新
```

## 数据维护（CLI）

在插件目录下可手动维护离线数据：

```bash
# 增量更新（与「泰拉更新」相同逻辑）
python prepare_data.py

# 全量重建
python prepare_data.py --force

# 本地维护：规范化图片名 + 同步套装部件英文
python prepare_data.py --resync-pieces

# 从 Wiki 重新抓取所有套装页（盔甲 + 时装）
python prepare_data.py --refresh-sets

# 仅重新抓取套装英文并同步部件（较快，推荐在 refresh-sets 后或 en 不同步时使用）
python prepare_data.py --resync-set-en

# 仅回填英文
python prepare_data.py --en-only
```

## 数据来源

- [官方中文 Terraria Wiki](https://terraria.wiki.gg/zh/wiki/Terraria_Wiki)
- [官方英文 Terraria Wiki](https://terraria.wiki.gg/wiki/Terraria_Wiki)

## 开发与测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

## 要求

- AstrBot >= 4.16
- Python 依赖见 `requirements.txt`
