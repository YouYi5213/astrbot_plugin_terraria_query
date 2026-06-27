# astrbot_plugin_terraria_query

泰拉瑞亚 Wiki 离线查询 AstrBot 插件。发送 `/泰拉查询 <物品名>`，以图片卡片展示物品属性与合成配方，支持中文模糊匹配。

## 功能

- `/泰拉查询 <物品名>` — 离线查询物品信息
- 中文模糊匹配（如「天顶」→「天顶剑」）
- 图片卡片展示属性与合成配方，失败时降级为文本

## 安装

1. 将本仓库克隆到 AstrBot 的 `data/plugins/` 目录
2. 安装依赖（AstrBot 通常会自动处理）：

```bash
pip install -r requirements.txt
```

3. 在 **AstrBot 根目录** 运行数据准备脚本（仅需一次）：

```bash
python data/plugins/astrbot_plugin_terraria_query/prepare_data.py
```

4. 在 AstrBot WebUI 重载插件

## 使用示例

```
/泰拉查询 天顶剑
/泰拉查询 天顶
/泰拉查询 治疗药水
```

## 数据来源

- [官方中文 Terraria Wiki](https://terraria.wiki.gg/zh/wiki/Terraria_Wiki)

## 要求

- AstrBot >= 4.16
- Python 依赖见 `requirements.txt`
