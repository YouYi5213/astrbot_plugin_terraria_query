# astrbot_plugin_terraria_query

泰拉瑞亚 Wiki 离线查询 AstrBot 插件。发送 `泰拉查询 <名称>` 或 `泰拉 <名称>`（无需 `/` 前缀），以**中文**图片卡片展示物品属性、合成配方或生物群系介绍；仅支持**中文名称**搜索。

## 功能

- **物品查询** — `泰拉查询 天顶剑` / `泰拉 天顶剑`
- **中文模糊匹配** — 如「天顶」→「天顶剑」
- **图片卡片** — 属性、描述、合成配方、掉落来源；失败时降级为文本
- **盔甲/时装套装** — 分部件展示属性与配方（如寒霜盔甲、兔兔套装）
- **套装部件** — 可单独查询（如 `泰拉 钛金面具`）
- **翅膀** — 支持总览页与按名称查询（如猪龙鱼之翼）
- **坐骑召唤物** — 独立查询（如 `泰拉 虾松露` / `泰拉 可爱猪龙鱼`）
- **宠物召唤物** — 独立查询（如 `泰拉 蚊子琥珀` / `泰拉 恐龙宝宝`）
- **生物群系** — 横幅图 + 描述（如 `泰拉 森林` / `泰拉 地下层` / `泰拉 腐化`）

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
| `泰拉查询 <名称>` / `泰拉 <名称>` | 查询物品或生物群系（中文名称搜索，中文卡片展示） |
| `泰拉更新` | 从 Wiki **增量**更新（新增物品、刷新套装、回填描述等） |
| `泰拉强制更新` | **全量重建**（管理员，耗时较长） |

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
泰拉更新
```

## 数据维护（CLI）

在插件目录下可手动维护离线数据：

```bash
# 增量更新（与「泰拉更新」相同逻辑）
python prepare_data.py

# 全量重建
python prepare_data.py --force

# 本地维护：规范化图片名 + 同步套装部件
python prepare_data.py --resync-pieces

# 从 Wiki 重新抓取所有套装页（盔甲 + 时装）
python prepare_data.py --refresh-sets

# 移除 categories 中的 en / en_name / aliases 字段
python prepare_data.py --strip-en

# 从旧版根目录 JSON 迁移到 categories/（一次性）
python prepare_data.py --split-categories --remove-legacy

# 仅抓取坐骑 / 宠物 / 生物群系
python prepare_data.py --ingest-mounts
python prepare_data.py --ingest-pets
python prepare_data.py --ingest-biomes
```

## 数据来源

- [官方中文 Terraria Wiki](https://terraria.wiki.gg/zh/wiki/Terraria_Wiki)

## 开发与测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

## 更新失败排查（GitHub 镜像站）

若 WebUI 更新报错 `400 Bad Request`，且日志中出现 `gh-proxy.com/.../zipball/...`，说明镜像站不支持 GitHub API 的 zipball 链接。

**任选其一：**

1. **临时关闭镜像** — AstrBot WebUI → 插件 → 关闭 GitHub 镜像站，再点更新
2. **手动安装** — 从 [Releases](https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases) 下载 `astrbot_plugin_terraria_query-x.y.z.zip`，解压覆盖到 `data/plugins/astrbot_plugin_terraria_query/`
3. **v1.5.1 起** — 插件 `metadata.yaml` 已改为 `repo: .../tree/main`，启用镜像站时走 `github.com/.../archive/` 下载，一般可正常更新

## 要求

- AstrBot >= 4.16
- Python 依赖见 `requirements.txt`
