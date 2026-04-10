---
name: stock-quant-trading
description: A股/港股/美股量化交易入门资源汇总，包含开源框架、API接口、教程链接及止损止盈脚本模板。适用于超短线自动交易、量化策略研究的学习起点。
trigger: 股票自动交易、量化交易学习、超短线、止损止盈
---

# stock-quant-trading

A股/港股/美股量化交易入门资源汇总。

## 📌 资源总览

见 `references/` 目录下的分主题整理。

## 🗂️ 目录结构

```
stock-quant-trading/
├── SKILL.md            # 本文件，技能核心定义
├── references/          # 分主题资源汇总
│   ├── 开源框架.md
│   ├── 数据接口.md
│   └── 教程文章.md
├── scripts/            # 脚本模板
│   └── stop_loss_take_profit_example.py
└── CHANGELOG.md
```

## ⚡ 核心功能

- [x] 止损止盈 Python 脚本模板（ccxt / 券商 API 两个版本）
- [x] A股主流开源框架对比
- [x] 数据接口汇总
- [ ] 实盘对接指南（待补充券商专属方案）

## 📥 贡献指南

发现更好资源？PR 欢迎。以下格式新增：

```markdown
### 标题
- 描述
- 链接
- 推荐理由
```
