# Dayta_Skills 🐾

> Dayta 的个人技能库，存放 AI 助手 Skill 的开发与积累。

## 📁 仓库结构

```
Dayta_Skills/
├── README.md                        # 仓库说明
├── SKILLS.md                        # 技能索引（所有技能目录）
├── skills/                          # 技能存放目录
│   └── [skill-name]/                # 单个技能文件夹
│       ├── SKILL.md                 # 技能核心定义（必须）
│       ├── scripts/                 # 关联脚本
│       ├── references/              # 参考资料 / 文档
│       ├── assets/                  # 图片、图标等资源
│       └── CHANGELOG.md             # 更新日志
└── .github/
    └── workflows/
        └── validate-skills.yml      # CI 验证 workflow
```

## 🔧 如何添加新 Skill

1. 在 `skills/` 下创建以技能名称命名的文件夹
2. 编写 `SKILL.md`（核心定义文件，必需）
3. 按需添加 `scripts/`、`references/`、`assets/`
4. 更新 `SKILLS.md` 添加索引条目
5. 提交 PR / 直接推送到 main 分支

## 📖 SKILL.md 标准格式

```markdown
---
name: my-skill
description: 简短描述这个技能的作用
trigger: 触发关键词或场景
---

# My Skill

## 简介
...

## 使用方法
...

## 示例
...
```

## 👤 所有者

Dayta / Hu-zihaoOo
