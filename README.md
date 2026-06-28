# astrbot_plugin_terraria_query

泰拉瑞亚 Wiki 离线查询 AstrBot 插件。发送 `泰拉查询 <名称>` 或 `泰拉 <名称>`（无需 `/` 前缀），以**中文**图片卡片展示物品属性、合成配方、Boss 信息或生物群系介绍；仅支持**中文名称**搜索。

## 功能

- **物品查询** — `泰拉查询 天顶剑` / `泰拉 天顶剑`
- **中文模糊匹配** — 如「天顶」→「天顶剑」
- **图片卡片** — 属性、描述、合成配方、掉落来源；失败时降级为文本
- **盔甲/时装套装** — 分部件展示属性与配方（如寒霜盔甲、兔兔套装）
- **套装部件** — 可单独查询（如 `泰拉 钛金面具`）
- **翅膀** — 支持总览页与按名称查询（如猪龙鱼之翼）
- **坐骑召唤物** — 独立查询（如 `泰拉 虾松露` / `泰拉 可爱猪龙鱼`）
- **宠物召唤物** — 独立查询（如 `泰拉 蚊子琥珀` / `泰拉 恐龙宝宝`）
- **生物群系** — 横幅图 + 描述 + 条件（如 `泰拉 森林` / `泰拉 地下层` / `泰拉 腐化`）；加「内容」后缀查看内容表（如 `泰拉 森林内容`）
- **事件** — 横幅图 + 描述 + 条件（如 `泰拉 血月`）；加「内容」后缀查看内容表（如 `泰拉 血月内容`）
- **Boss** — 图像 + 分难度属性/掉落；多部位 Boss 展示部位信息（如 `泰拉 月亮领主` / `泰拉 克苏鲁之眼`）
- **城镇 NPC** — 描述、生成条件、商店与偏好（如 `泰拉 军火商`）

## 安装

1. 将本仓库克隆到 AstrBot 的 `data/plugins/` 目录，或通过 WebUI 从 GitHub Release 安装
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 插件已自带 `data/terraria_query/categories/` 离线数据，安装后可直接使用
4. 在 AstrBot **WebUI → 插件 → 泰拉瑞亚查询** 中配置（可选）：
   - **定时更新 Wiki 数据**：Cron 表达式，如 `0 4 * * 0`
   - **更新指令管理员 ID**：限制谁可以执行更新指令
5. 在 WebUI 重载插件

## 指令

| 指令 | 说明 |
|------|------|
| `泰拉查询 <名称>` / `泰拉 <名称>` | 查询物品、Boss、NPC、生物群系或事件（中文名称搜索，中文卡片展示） |
| `泰拉更新` | 从 Wiki **增量**更新离线数据（物品、坐骑/宠物、群系/事件、Boss、NPC、缺失图片等；与 `python prepare_data.py` 相同） |
| `泰拉强制更新` | **全量重建**（管理员，耗时较长；与 `python prepare_data.py --force` 相同） |

> 群聊中无需 `/` 前缀；`/泰拉查询` 等写法仍然有效。

## 使用示例

```
泰拉 天顶剑
泰拉 天顶
泰拉 钛金盔甲
泰拉 钛金面具
泰拉 寒霜头盔
泰拉 翅膀
泰拉 虾松露
泰拉 可爱猪龙鱼
泰拉 蚊子琥珀
泰拉 恐龙宝宝
泰拉 森林
泰拉 地下层
泰拉 腐化
泰拉 月亮领主
泰拉 克苏鲁之眼
泰拉 军火商
泰拉更新
```

## 数据维护

**推荐**：在 AstrBot 对话中向机器人发送 **`泰拉更新`**（若 WebUI 配置了「更新指令管理员 ID」，仅该管理员可执行）。  
会增量同步 Wiki 离线数据：物品、套装、坐骑/宠物、生物群系/事件、Boss、城镇 NPC，并补全缺失图片。**一般无需手动跑脚本。**

**手动维护**（开发调试，或 Bot 所在环境不方便发消息时）：在 **本插件目录** 下打开终端执行（路径因安装方式而异，常见如下）：

- WebUI / Release 安装：`AstrBot/data/plugins/astrbot_plugin_terraria_query/`
- 本仓库开发：克隆后的 `astrbot_plugin_terraria_query/` 根目录

需已安装 `requirements.txt` 依赖，且机器可访问 [terraria.wiki.gg](https://terraria.wiki.gg)。

```bash
cd AstrBot/data/plugins/astrbot_plugin_terraria_query   # 按实际路径修改

python prepare_data.py              # 增量（与「泰拉更新」相同）
python prepare_data.py --force      # 全量重建（与「泰拉强制更新」相同）
```

<details>
<summary>高级选项（日常维护一般不需要）</summary>

```bash
python prepare_data.py --resync-pieces          # 仅本地：规范化图片名 + 同步套装部件
python prepare_data.py --refresh-sets           # 仅重新抓取所有套装页
python prepare_data.py --desc-only              # 仅回填物品描述
python prepare_data.py --backfill-content-images  # 仅补群系/事件内容区缺失图片
python prepare_data.py --backfill-boss-images     # 仅补 Boss 缺失图片
python prepare_data.py --ingest-mounts          # 仅抓取坐骑（单独调试）
python prepare_data.py --ingest-pets            # 仅抓取宠物
python prepare_data.py --ingest-biomes          # 仅抓取生物群系
python prepare_data.py --ingest-events          # 仅抓取事件
python prepare_data.py --ingest-bosses          # 仅抓取 Boss
python prepare_data.py --split-categories --remove-legacy  # 旧版 JSON 一次性迁移
```

</details>

## 数据来源

- [官方中文 Terraria Wiki](https://terraria.wiki.gg/zh/wiki/Terraria_Wiki)

## 开发与测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

## 更新失败排查（GitHub 镜像站）

若 WebUI 更新报错 `400 Bad Request`，且日志中出现 `gh-proxy.com/.../zipball/...`，说明当前镜像站不支持 GitHub API 的 zipball 链接。

**任选其一：**

1. **临时关闭镜像** — AstrBot WebUI → 插件 → 关闭 GitHub 镜像站，再点更新
2. **手动安装** — 从 [Releases](https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases) 下载 `astrbot_plugin_terraria_query-x.y.z.zip`，解压覆盖到 `data/plugins/astrbot_plugin_terraria_query/`

**说明：** `metadata.yaml` 的 `repo` 应写仓库根地址（与 [twrpg 查询插件](https://github.com/YouYi5213/astrbot_plugin_twrpg_query) 相同），AstrBot 会按 **最新 Release 标签** 下载（如 `zipball/v1.6.8`）。若写成 `.../tree/main`，则会改为下载整份 `main` 分支 zip，包体更大、通常更慢。

## 要求

- AstrBot >= 4.16
- Python 依赖见 `requirements.txt`
