# Lark Bot

Lark/飞书 IM bot，由 Claude Code 驱动。

## 功能特性

- 🤖 **Claude 驱动**：使用 Claude Code 作为智能助手核心
- 📂 **项目管理**：支持多项目切换，每个项目独立会话
- 🛠️ **Skill 系统**：支持全局和项目级 Skills，自动发现和使用记录
- 🔒 **Skill 锁定**：可锁定 Skill，后续消息自动通过该 Skill 处理
- 💾 **会话续接**：自动续接历史会话，保持上下文连续性
- 🔄 **进程守护**：自带守护进程，崩溃自动重启

## 安装

### 从 PyPI 安装（推荐）

```bash
pip install lark-bot
```

### 从源码安装

```bash
git clone https://github.com/xxx/lark-bot
cd lark-bot
pip install -e .
```

## 配置

### 1. 创建配置文件

```bash
mkdir -p ~/.config/lark-bot
cp config.example.yaml ~/.config/lark-bot/config.yaml
```

编辑配置文件：

```yaml
# 数据目录
projects_root: ~/lark-projects
sessions_file: ~/.local/share/lark-bot/sessions.json
log_dir: ~/.local/share/lark-bot/logs

# Claude 配置
claude:
  model: claude-sonnet-4-6
  timeout: 300

# Skills 配置
skills:
  global_dir: ~/.claude/skills

# 守护进程配置
daemon:
  restart_delay: 5
```

### 2. 配置 Lark CLI

确保已配置 `lark-cli`：

```bash
lark-cli config init
lark-cli auth login
```

## 运行

### 方式 1：直接运行

```bash
lark-bot start
```

### 方式 2：Systemd Service（推荐生产环境）

```bash
# 安装服务
lark-bot install-service

# 启动服务
systemctl --user start lark-bot

# 查看状态
systemctl --user status lark-bot

# 停止服务
systemctl --user stop lark-bot
```

## CLI 命令

| 命令 | 说明 |
|---|---|
| `lark-bot start` | 启动服务（带守护） |
| `lark-bot start --no-daemon` | 单次运行（调试用） |
| `lark-bot status` | 查看状态 |
| `lark-bot switch <name> --chat-id <id>` | 切换项目 |
| `lark-bot list-projects` | 列出项目 |
| `lark-bot list-skills` | 列出 Skills |
| `lark-bot install-service` | 安装 systemd 服务 |

## 飞书中命令

| 命令 | 说明 |
|---|---|
| `/p:name` | 切换/创建项目 |
| `/pl` | 列出项目 |
| `/pd:name` | 删除项目 |
| `/sl` | 列出 Skills（最近使用排前） |
| `/sl 2` | 翻到第2页 |
| `/sl all` | 显示全部 Skills |
| `/sl 关键词` | 搜索 Skills |
| `/s:name` | 调用指定 Skill |
| `/s+name` | 锁定 Skill |
| `/s-` | 解锁 Skill |
| `/s?` | 查看当前锁定 |
| `/status` | 查看状态 |

## Skill 使用示例

### 基本使用

```
# 列出所有可用的 Skills
/sl

# 调用 lark-calendar Skill
/s:lark-calendar 今天日程

# 调用 Skill + shortcut
/s:lark-calendar +agenda
```

### Skill 锁定功能

锁定后，后续消息自动通过该 Skill 处理：

```
# 锁定 lark-calendar Skill
/s+lark-calendar

# 之后发的消息会自动走日历 Skill
今天有什么会？

# 解锁
/s-
```

### Skill 搜索

```
# 搜索包含"日历"的 Skills
/sl 日历

# 搜索包含"邮件"的 Skills
/sl 邮件
```

## 项目结构

```
lark-bot/
├── pyproject.toml          # 项目元数据
├── README.md               # 本文档
├── config.example.yaml     # 配置文件示例
├── src/
│   └── lark_bot/
│       ├── __init__.py     # 包初始化
│       ├── __main__.py     # python -m 入口
│       ├── cli.py          # CLI 命令
│       ├── bot.py          # 核心逻辑
│       ├── config.py       # 配置管理
│       ├── project.py      # 项目管理
│       ├── skills.py       # Skill 管理
│       └── session.py      # Session 管理
└── scripts/
    └── install-service.sh  # Systemd 安装脚本（已弃用，使用 CLI）
```

## 开发

### 安装开发依赖

```bash
pip install -e ".[dev]"
```

### 运行测试

```bash
pytest
```

### 代码格式化

```bash
black src/
ruff check src/
```

## 常见问题

### 1. 如何查看日志？

```bash
tail -f ~/.local/share/lark-bot/logs/bot.log
```

### 2. 如何重启服务？

```bash
systemctl --user restart lark-bot
```

### 3. 如何查看当前会话？

```bash
lark-bot status
```

### 4. 项目数据存储在哪里？

- 项目目录：`~/lark-projects/<project-name>/`
- 会话数据：`~/.claude/projects/<mapped-path>/*.jsonl`
- Skill 使用记录：`~/lark-projects/<project-name>/.claude/skill_usage.json`
- Sessions 映射：`~/.local/share/lark-bot/sessions.json`

## License

MIT
