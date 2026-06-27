# 更新日志

本文件记录 [泰拉瑞亚查询](https://github.com/YouYi5213/astrbot_plugin_terraria_query) 插件的版本变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本号与 GitHub Release 一致。

---

## [1.4.7] - 2026-06-27

### 新增

- **坐骑召唤物**独立数据 `mounts.json`（37 种，以 Wiki「坐骑」总览页物品栏为准）
- **宠物召唤物**独立数据 `pets.json`（84 种，以 Wiki「宠物」总览页物品栏为准）
- 查询支持坐骑/宠物召唤**物品**名称、英文名，以及 buff / 坐骑 / 宠物实体名（如「虾松露」「可爱猪龙鱼」「恐龙宝宝」）
- 卡片新增「给予增益」「召唤坐骑」「召唤宠物」区块（`card_v27_`）
- CLI：`--ingest-mounts`、`--ingest-pets`；审计脚本 `scripts/audit_mounts.py`、`scripts/audit_pets.py`

### 修复

- 宠物总览表简称（如「鱼」「暗影珠」「猩红之心」）正确映射到 Wiki 物品页（如「鱼（物品）」）

---

## [1.4.6] - 2026-06-27

### 修复

- 修复描述误用工具提示的问题（如「环境改造枪」等 117 条），回填 Wiki 完整导语
- 描述抓取不再将工具提示当作成功结果；自动检测并重新抓取
- 卡片缓存更新为 `card_v25_`

---

## [1.4.5] - 2026-06-27

### 变更

- **中文展示 + 英文名可搜**：`泰拉 Clentaminator` 等英文查询仍命中物品，但卡片统一显示中文
- 移除 `items.json` 中的 `en` 数据块，仅保留 `en_name` 供搜索（体积显著减小）
- 删除英文卡片 / 英文全量回填逻辑；CLI 移除 `--en-only`、`--resync-set-en`，新增 `--strip-en`

### 修复

- 修复中文物品误显示英文描述（如「环境改造枪」）；描述抓取仅走中文 Wiki
- 卡片缓存更新为 `card_v24_`

---

## [1.4.4] - 2026-06-27

### 新增

- 描述区支持 Wiki 金币图标（购买价 / 出售价等，如自动锤炼机 1 铂金币）
- 「泰拉更新」会自动回填缺失金币图标的描述（约 251 条）

### 修复

- 卡片缓存更新为 `card_v23_`

---

## [1.4.3] - 2026-06-27

### 修复

- 修复 v1.4.2 卡片生成失败：恢复 `_load_inline_icon`，解决钛金盔甲等套装查询退化为文字的问题
- 文本降级时增加「属性」区块标题，避免与描述混淆

### 新增

- 新增 **家具 / 其他** 分类数据（+245 条，共 2193 条）
- 可查询炼药桌、炼金瓶、工作台等制作站与家具
- 「炼药瓶」作为「炼金瓶」的搜索别名
- CLI：`python prepare_data.py --ingest-categories` 仅抓取新分类条目

---

## [1.4.2] - 2026-06-27

### 新增

- **时装套装**分部件展示（兔兔套装、狗狗套装等 11 套）
- 265 个套装部件支持英文查询（如 `泰拉 Titanium Mask`）
- 新增 `泰拉强制更新` 指令（全量重建，管理员）
- CLI：`--resync-pieces` / `--refresh-sets` / `--resync-set-en`
- 新增 `tests/` 与 `requirements-dev.txt`

### 修复

- 修复英文 Wiki `ArmorSet` / `VanitySet` 类型无法识别的问题
- 图片名规范化（`17px-` 缩略图前缀），主图 / 配方图回退加载
- 文本降级支持套装部件与富文本属性

### 变更

- 卡片缓存 `card_v21_`，自动清理旧版本缓存

---

## [1.4.1] - 2026-06-27

### 修复

- 修复多部件盔甲防御行中头盔图标不显示（如钛金面具 / 头盔 / 头饰）
- Wiki 缩略图名自动回退到本地完整物品图

---

## [1.4.0] - 2026-06-27

### 新增

- **盔甲套装**按 Wiki「套装」面板分部件展示属性与合成配方
- 支持多头盔变体（钛金盔甲的面具 / 头盔 / 头饰等）
- 套装部件展开为独立可搜条目（如 `泰拉 寒霜头盔`）
- 回填 67 套盔甲、229 个部件数据与图标

---

## [1.3.9] - 2026-06-27

### 修复

- 修复翅膀备注中 `↷ 跳` 等按键图标显示为叉号
- 按键符号使用专用符号字体，兼容 Linux 环境
- 翅膀最大水平速度单位改回 `mph`

---

## [1.3.8] - 2026-06-27

### 修复

- 稀有度优先采用 Wiki 链接 title（如 Yellow → 黄色）
- 重写 sortkey fallback 表，猪龙鱼之翼等显示为黄色而非紫色

---

## [1.3.7] - 2026-06-27

### 修复

- 修正翅膀对比表列错位与单位显示
- 飞行时间 `s` → `秒`；备注移出属性区

---

## [1.3.6] - 2026-06-27

### 新增

- 从 Wiki「翅膀」对比表展开 46 个翅膀子条目
- 支持按翅膀名称直接查询（如 `泰拉 猪龙鱼之翼`）

---

## [1.3.5] - 2026-06-27

### 修复

- 修复「翅膀」等 Wiki 物品总览页查询
- 总览页不再误显示页面内多行合成表

---

## [1.3.4] - 2026-06-27

### 新增

- 描述区支持 Wiki 游戏按键图标（⚷ 打开/激活、⚒ 使用/攻击等）
- 按键以圆角徽章内联显示

---

## [1.3.3] - 2026-06-27

### 新增

- 物品卡片新增 Wiki 导语「描述」区块
- 1637 个物品描述已全部回填

---

## [1.3.2] - 2026-06-27

### 修复

- 查询优先精确匹配，减少子串误匹配（如「阴森木」不再带出「阴森木墙」）

---

## [1.3.1] - 2026-06-27

### 修复

- 英文查询显示英文实体名与难度标签
- 英文配方材料 / 产物数量正确显示
- 配方材料按卡片宽度自动换行

---

## [1.3.0] - 2026-06-27

### 新增

- 卡片新增「来自」区块，显示敌怪掉落来源
- 三难度一致时合并显示，不一致时分难度标注
- 内置全量掉落数据，安装即用

### 修复

- 属性标签右对齐；图标等比缩放
- 配方支持数量显示；工具提示内联平台图标

---

## [1.2.x] - 2026-06

### 新增 / 修复

- 内置中文字体，修复 Docker / Linux 中文方框
- 稀有度显示颜色名称；卖出价显示金币图标
- 支持中英文双语查询与英文 Wiki 卡片
- 卡片字体与材料列表布局优化

---

## [1.1.x] - 2026-06

### 修复

- 群聊无需 `/` 前缀即可触发（`泰拉查询` / `泰拉更新`）
- 修复插件加载 `No module named prepare_data` 问题

---

## [1.0.0] - 2026-06

### 新增

- 泰拉瑞亚 Wiki 离线查询插件首次发布
- 支持 `泰拉查询 <物品名>` 图片卡片展示
- 中文模糊匹配与合成配方显示

---

[1.4.3]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.4.3
[1.4.2]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.4.2
[1.4.1]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.4.1
[1.4.0]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.4.0
[1.3.9]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.3.9
[1.3.8]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.3.8
[1.3.7]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.3.7
[1.3.6]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.3.6
[1.3.5]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.3.5
[1.3.4]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.3.4
[1.3.3]: https://github.com/YouYi5213/astrbot_plugin_terraria_query/releases/tag/v1.3.3
