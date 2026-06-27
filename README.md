# astrbot_plugin_terraria_query

泰拉瑞亚 Wiki 离线查询 AstrBot 插件。发送 `泰拉查询 <物品名>`（无需 `/` 前缀），以图片卡片展示物品属性与合成配方，支持中文模糊匹配。

## 功能

- `泰拉查询 <物品名>` — 离线查询物品信息（支持中文/英文，如 `天顶剑` / `Zenith`）
- 中文模糊匹配（如「天顶」→「天顶剑」）
- 图片卡片展示属性与合成配方，失败时降级为文本

## 安装

1. 将本仓库克隆到 AstrBot 的 `data/plugins/` 目录
2. 安装依赖（AstrBot 通常会自动处理）：

```bash
pip install -r requirements.txt
```

3. 插件已自带 `data/terraria_query/` 离线数据，克隆后可直接使用
4. 在 AstrBot **WebUI → 插件 → 泰拉瑞亚查询** 中配置（可选）：
   - **定时更新 Wiki 数据**：Cron 表达式，如 `0 4 * * 0`（每周日 4:00 增量更新）
   - **更新指令管理员 ID**：限制谁可以执行 `泰拉更新`
5. 在 WebUI 重载插件

## 指令

| 指令 | 说明 |
|------|------|
| `泰拉查询 <物品名>` | 查询物品，支持中文/英文模糊匹配（如 `天顶剑` / `Zenith`） |
| `泰拉更新` | 从 Wiki **增量**更新数据（仅新增，跳过已有） |

> 群聊中无需 `/` 前缀即可触发；`/泰拉查询` 等写法仍然有效。

## 使用示例

```
泰拉查询 天顶剑
泰拉查询 Zenith
泰拉 治疗药水
泰拉更新
```

## 数据来源

- [官方中文 Terraria Wiki](https://terraria.wiki.gg/zh/wiki/Terraria_Wiki)
- [官方英文 Terraria Wiki](https://terraria.wiki.gg/wiki/Terraria_Wiki)（英文查询时使用）

> 英文数据通过 Wiki 语言链接与物品图片名自动关联。部分物品可能暂无英文条目，可尝试中文查询，或通过「泰拉更新」增量回填。

## 要求

- AstrBot >= 4.16
- Python 依赖见 `requirements.txt`
