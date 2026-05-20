"""核心逻辑：读取 stdin 事件并分发。"""

import sys
import os
import json
import subprocess
import logging
import threading
import datetime
from pathlib import Path

from .config import load_config, expand_path
from .session import SessionManager
from .project import ProjectManager
from .skills import SkillManager, relative_time

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

# 全局实例
_config = None
_session_mgr = None
_project_mgr = None
_skill_mgr = None


def _init_globals():
    """初始化全局管理器（延迟加载，支持测试覆盖）。"""
    global _config, _session_mgr, _project_mgr, _skill_mgr
    if _config is None:
        _config = load_config()
        _session_mgr = SessionManager(_config)
        _project_mgr = ProjectManager(_config)
        _skill_mgr = SkillManager(_config, _project_mgr)


# 后台项目输出缓冲区：(chat_id, project_name) → [messages]
project_buffer: dict[tuple[str, str], list[str]] = {}
buffer_lock = threading.Lock()

# 正在执行的任务：(chat_id, project_name)
active_tasks: set[tuple[str, str]] = set()
active_lock = threading.Lock()

# 状态命令
STATUS_COMMANDS = {"/status", "/状态", "/ping"}
HELP_COMMANDS = {"/help", "/帮助"}
PAGE_SIZE = 5


def send_reply(chat_id: str, text: str):
    """发送消息回复。"""
    _init_globals()
    cmd = ["lark-cli"]
    if _config.get("profile"):
        cmd += ["--profile", _config["profile"]]
    cmd += ["im", "+messages-send", "--as", "bot", "--chat-id", chat_id, "--text", text]
    subprocess.run(
        cmd,
        check=True, stdin=subprocess.DEVNULL,
    )


def send_to_project(chat_id: str, pname: str, text: str):
    """发送消息到指定项目：当前项目立即发送，后台项目暂存缓冲区。"""
    _init_globals()
    current = _session_mgr.get_current_project(chat_id)
    if current == pname:
        try:
            send_reply(chat_id, text)
        except Exception as e:
            logging.warning("send_reply failed: %s", e)
    else:
        with buffer_lock:
            project_buffer.setdefault((chat_id, pname), []).append(text)
        logging.debug("buffered for project=%s: %r", pname, text[:50])


def make_hint(tool_name: str, tool_input: dict) -> str:
    """生成工具调用的提示文本。"""
    if tool_name == "Agent":
        desc = tool_input.get("description", "")
        return f"🤖 启动 subagent：{desc[:40]}" if desc else "🤖 正在启动 subagent..."
    if tool_name == "WebSearch":
        q = tool_input.get("query", "")
        return f"🔍 搜索：{q[:50]}" if q else "🔍 正在搜索网络..."
    if tool_name == "WebFetch":
        url = tool_input.get("url", "")
        return f"🌐 读取：{url[:60]}" if url else "🌐 正在读取网页..."
    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip().splitlines()[0][:40]
        return f"⚙️ 执行：{cmd}" if cmd else "⚙️ 正在执行命令..."
    return {
        "Write": "📝 正在写入文件...",
        "Edit": "✏️ 正在编辑文件...",
        "Read": "📖 正在读取文件...",
        "Skill": "🛠️ 正在调用技能...",
    }.get(tool_name, "")


def call_claude(chat_id: str, message: str, pname: str) -> str:
    """调用 Claude 处理消息。"""
    _init_globals()
    pdir = _project_mgr.ensure_dir(pname)

    model = _config.get("claude", {}).get("model", "claude-sonnet-4-6")
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
           "--dangerously-skip-permissions",
           "--setting-sources", "user,project,local",
           "--model", model]

    # 注入 CLAUDE.md
    claude_md = os.path.join(pdir, "CLAUDE.md")
    if os.path.exists(claude_md):
        with open(claude_md) as f:
            content = f.read().strip()
        if content:
            cmd += ["--append-system-prompt", content]

    if _project_mgr.has_session(pname):
        cmd.append("--continue")
    cmd.append(message)

    log_dir = Path(expand_path(_config.get("log_dir", "~/.lark-bot/logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    stderr_log = (log_dir / "claude_stderr.log").open("a")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_log,
                           text=True, stdin=subprocess.DEVNULL, cwd=pdir,
                           env={**os.environ, **{k: str(v) for k, v in _config.get("claude", {}).get("env", {}).items()}})
    result_text = "（无回复）"
    sent_hints: set[str] = set()

    with active_lock:
        active_tasks.add((chat_id, pname))
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            ev_type = ev.get("type")

            if ev_type == "assistant":
                is_subagent = bool(ev.get("parent_tool_use_id"))
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        hint = make_hint(block.get("name", ""), block.get("input") or {})
                        if hint and hint not in sent_hints:
                            sent_hints.add(hint)
                            send_to_project(chat_id, pname, hint)
                    elif block.get("type") == "text" and is_subagent:
                        text = block.get("text", "").strip()
                        if text:
                            send_to_project(chat_id, pname, text)

            elif ev_type == "result":
                if ev.get("subtype") == "success":
                    result_text = ev.get("result", result_text)
                else:
                    result_text = "抱歉，处理出错了。"
                logging.info("chat_id=%s project=%s done", chat_id, pname)

        proc.wait()
        if proc.returncode != 0 and result_text == "（无回复）":
            result_text = "抱歉，处理出错了。"
    finally:
        with active_lock:
            active_tasks.discard((chat_id, pname))
    return result_text


def handle_status(chat_id: str):
    """处理状态查询。"""
    _init_globals()
    lines = []

    # 服务状态
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "lark-bot"],
                          capture_output=True, text=True)
        svc = r.stdout.strip()
        lines.append(f"{'🟢' if svc == 'active' else '🔴'} 服务：{svc}")
    except Exception:
        lines.append("❓ 服务：未知")

    pname = _session_mgr.get_current_project(chat_id)
    projects = _session_mgr.get_projects(chat_id)

    if pname:
        with buffer_lock:
            pending = len(project_buffer.get((chat_id, pname), []))
        lines.append(f"📂 项目：{pname}（共 {len(projects)} 个）")
        lines.append(f"📁 目录：{_project_mgr.project_dir(pname)}")

        # 锁定的 skill
        locked = _skill_mgr.get_locked(pname)
        if locked:
            lines.append(f"🔒 锁定 skill：{locked}")

        if pending:
            lines.append(f"📬 待读：{pending} 条（后台输出）")
        info = _project_mgr.get_session_info(pname)
        if info:
            lines.append(f"🔑 会话：{info['session_id']}...")
            lines.append(f"📄 记录：{info['lines']} 条，{info['size_kb']}KB，更新 {info['mtime']}")
        else:
            lines.append("🆕 会话：尚未建立")
    else:
        lines.append("⚠️ 未设置项目，发送 /p:名称 创建")

    with active_lock:
        running = [p for (c, p) in active_tasks if c == chat_id]
    if running:
        lines.append(f"⚙️ 正在处理：{', '.join(running)}")
    else:
        lines.append("✅ 当前空闲")

    try:
        send_reply(chat_id, "\n".join(lines))
    except Exception as e:
        logging.error("status reply failed: %s", e)


def handle_project_list(chat_id: str):
    """处理项目列表。"""
    _init_globals()
    current = _session_mgr.get_current_project(chat_id)
    projects = _session_mgr.get_projects(chat_id)

    if not projects:
        try:
            send_reply(chat_id, "📂 暂无项目，发送 /p:名称 创建")
        except Exception:
            pass
        return

    lines = ["📂 项目列表："]
    for name in projects:
        marker = "▶ " if name == current else "  "
        tag = "（有历史）" if _project_mgr.has_session(name) else "（新）"
        with buffer_lock:
            pending = len(project_buffer.get((chat_id, name), []))
        if pending:
            tag += f" [{pending} 条待读]"
        lines.append(f"{marker}{name} {tag}")

    try:
        send_reply(chat_id, "\n".join(lines))
    except Exception as e:
        logging.error("project list reply failed: %s", e)


def handle_project_switch(chat_id: str, project_name: str):
    """处理项目切换。"""
    _init_globals()
    _project_mgr.ensure_dir(project_name)
    _session_mgr.set_current_project(chat_id, project_name)

    is_new = not _project_mgr.has_session(project_name)
    msg = f"✅ 已切换到项目：{project_name}\n📁 {_project_mgr.project_dir(project_name)}"
    msg += "\n（新项目，将开始新会话）" if is_new else "\n（已有历史，自动续接）"

    with buffer_lock:
        buffered = project_buffer.pop((chat_id, project_name), [])
    if buffered:
        msg += f"\n📬 期间有 {len(buffered)} 条输出，正在回放..."

    try:
        send_reply(chat_id, msg)
    except Exception as e:
        logging.error("project switch reply failed: %s", e)
        return

    for item in buffered:
        try:
            send_reply(chat_id, item)
        except Exception as e:
            logging.error("buffer flush failed: %s", e)


def handle_project_delete(chat_id: str, project_name: str):
    """处理项目删除。"""
    _init_globals()

    # 拒绝删除正在运行的项目
    with active_lock:
        is_running = (chat_id, project_name) in active_tasks
    if is_running:
        try:
            send_reply(chat_id, f"⚠️ 项目 {project_name} 正在执行任务，请等待完成后再删除。")
        except Exception:
            pass
        return

    was_current = _session_mgr.remove_project(chat_id, project_name)
    if not was_current:
        try:
            send_reply(chat_id, f"❌ 项目不存在：{project_name}")
        except Exception:
            pass
        return

    with buffer_lock:
        project_buffer.pop((chat_id, project_name), None)

    current = _session_mgr.get_current_project(chat_id)
    msg = f"🗑️ 已删除项目：{project_name}"
    if was_current and current:
        msg += f"\n已自动切换到：{current}"
    elif was_current:
        msg += "\n（无剩余项目，请用 /p:名称 创建新项目）"
    msg += f"\n（目录保留：{_project_mgr.project_dir(project_name)}）"
    try:
        send_reply(chat_id, msg)
    except Exception as e:
        logging.error("project delete reply failed: %s", e)


def handle_skill_list(chat_id: str, pname: str, keyword: str | None = None,
                     page: int = 1, show_all: bool = False):
    """处理 skill 列表。"""
    _init_globals()
    all_skills = _skill_mgr.list_all(pname)
    usage = _skill_mgr.load_usage(pname)

    # 关键词过滤
    if keyword:
        kw = keyword.lower()
        all_skills = [(n, d) for n, d in all_skills if kw in n.lower() or kw in d.lower()]
        if not all_skills:
            try:
                send_reply(chat_id, f"🔍 没有匹配「{keyword}」的 skill")
            except Exception:
                pass
            return

    # 按最近使用排序
    def sort_key(item):
        last = usage.get(item[0], {}).get("last_used", "")
        return last
    all_skills.sort(key=sort_key, reverse=True)

    total = len(all_skills)

    if show_all:
        lines = [f"🛠️ 全部 Skills（共 {total} 个）："]
        for i, (name, desc) in enumerate(all_skills, 1):
            u = usage.get(name)
            tag = f" 🔥({relative_time(u['last_used'])})" if u else ""
            lines.append(f"{i}. {name} — {desc}{tag}")
    else:
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(1, min(page, total_pages))
        start = (page - 1) * PAGE_SIZE
        page_skills = all_skills[start:start + PAGE_SIZE]

        lines = [f"🛠️ Skills（第{page}页，共{total_pages}页）："]
        for i, (name, desc) in enumerate(page_skills, 1):
            u = usage.get(name)
            tag = f" 🔥({relative_time(u['last_used'])})" if u else ""
            lines.append(f"{start + i}. {name} — {desc}{tag}")

    lines.append("发送 /sl <页码> 翻页，/sl all 全部，/sl <关键词> 搜索，/s:名称 调用")

    try:
        send_reply(chat_id, "\n".join(lines))
    except Exception as e:
        logging.error("skill list reply failed: %s", e)


def handle_skill_lock(chat_id: str, pname: str, skill_name: str):
    """处理 skill 锁定。"""
    _init_globals()
    all_skills = _skill_mgr.list_all(pname)
    names = [n for n, _ in all_skills]
    if skill_name not in names:
        try:
            send_reply(chat_id, f"❌ skill 不存在：{skill_name}")
        except Exception:
            pass
        return

    _skill_mgr.set_locked(pname, skill_name)
    _skill_mgr.record_use(pname, skill_name)
    try:
        send_reply(chat_id, f"🔒 已锁定 skill：{skill_name}\n后续消息将自动通过此 skill 处理\n发送 /s- 解锁")
    except Exception as e:
        logging.error("skill lock reply failed: %s", e)


def handle_skill_unlock(chat_id: str, pname: str):
    """处理 skill 解锁。"""
    _init_globals()
    locked = _skill_mgr.clear_locked(pname)
    if locked:
        try:
            send_reply(chat_id, f"🔓 已解锁 skill：{locked}")
        except Exception:
            pass
    else:
        try:
            send_reply(chat_id, "当前没有锁定的 skill")
        except Exception:
            pass


def handle_skill_status(chat_id: str, pname: str):
    """处理 skill 状态查询。"""
    _init_globals()
    locked = _skill_mgr.get_locked(pname)
    if locked:
        try:
            send_reply(chat_id, f"🔒 当前锁定：{locked}\n发送 /s- 解锁")
        except Exception:
            pass
    else:
        try:
            send_reply(chat_id, "当前未锁定任何 skill\n发送 /s+名称 锁定")
        except Exception:
            pass


def handle_help(chat_id: str):
    """显示帮助信息。"""
    msg = (
        "📖 Lark Bot 命令帮助\n\n"
        "📂 项目管理：\n"
        "/p:名称 — 切换/创建项目\n"
        "/pl — 列出项目\n"
        "/pd:名称 — 删除项目\n\n"
        "🛠️ Skill 操作：\n"
        "/sl — 列出 Skills（最近使用排前）\n"
        "/sl 2 — 翻页\n"
        "/sl all — 显示全部\n"
        "/sl 关键词 — 搜索\n"
        "/s:名称 — 调用 Skill\n"
        "/s:名称 参数 — 调用 Skill + 参数\n\n"
        "🔒 Skill 锁定：\n"
        "/s+名称 — 锁定 Skill（后续消息自动走它）\n"
        "/s- — 解锁\n"
        "/s? — 查看当前锁定\n\n"
        "📊 其他：\n"
        "/status — 查看状态\n"
        "/help — 显示本帮助\n\n"
        "💡 其他消息会发送给 Claude 处理"
    )
    try:
        send_reply(chat_id, msg)
    except Exception as e:
        logging.error("help reply failed: %s", e)


def handle_message(chat_id: str, content: str):
    """处理用户消息。"""
    _init_globals()
    pname = _session_mgr.get_current_project(chat_id)
    if not pname:
        try:
            send_reply(chat_id, "请先用 /p:项目名称 创建并切换到一个项目。")
        except Exception:
            pass
        return

    # 检查是否有锁定的 skill
    locked = _skill_mgr.get_locked(pname)
    if locked:
        content = f"/{locked} {content}"
        _skill_mgr.record_use(pname, locked)

    send_to_project(chat_id, pname, "⏳ 收到，正在处理...")
    reply = call_claude(chat_id, content, pname)
    logging.info("project=%s reply=%r", pname, reply[:80])
    send_to_project(chat_id, pname, reply)


def main():
    """主循环：读取 stdin 事件并分发。"""
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue

        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if event.get("type") != "im.message.receive_v1":
            continue

        chat_id = event.get("chat_id", "")
        content = event.get("content", "")
        if not chat_id or not content:
            continue

        logging.info("received chat_id=%s content=%r", chat_id, content)
        cmd = content.strip()

        # 初始化全局管理器
        _init_globals()

        # 命令分发
        if cmd in STATUS_COMMANDS:
            threading.Thread(target=handle_status, args=(chat_id,), daemon=True).start()
        elif cmd in HELP_COMMANDS:
            threading.Thread(target=handle_help, args=(chat_id,), daemon=True).start()
        elif cmd == "/pl":
            threading.Thread(target=handle_project_list, args=(chat_id,), daemon=True).start()
        elif cmd.startswith("/pd:"):
            pname = cmd[4:].strip()
            if pname:
                threading.Thread(target=handle_project_delete, args=(chat_id, pname), daemon=True).start()
        elif cmd == "/sl" or cmd.startswith("/sl "):
            pname = _session_mgr.get_current_project(chat_id)
            if not pname:
                try:
                    send_reply(chat_id, "⚠️ 请先用 /p:名称 创建项目")
                except Exception:
                    pass
            else:
                arg = cmd[3:].strip()
                if arg == "-h":
                    try:
                        send_reply(chat_id, (
                            "🛠️ Skill 使用帮助：\n\n"
                            "/sl — 列出 skills（最近使用排前，每页5个）\n"
                            "/sl 2 — 翻到第2页\n"
                            "/sl all — 显示全部 skills\n"
                            "/sl 日历 — 搜索含「日历」的 skills\n"
                            "/s:lark-calendar — 调用指定 skill\n"
                            "/s:lark-calendar +agenda — 调用 skill + shortcut\n\n"
                            "🔒 锁定功能：\n"
                            "/s+lark-calendar — 锁定 skill，后续消息自动走它\n"
                            "/s- — 解锁\n"
                            "/s? — 查看当前锁定\n\n"
                            "💡 示例：\n"
                            "/s+lark-calendar → 之后发「今天日程」自动走日历 skill\n"
                            "/s- → 解锁后恢复正常模式\n\n"
                            "项目级 skills 排在前面，全局 lark-* skills 在后面。\n"
                            "使用记录保存在项目的 .claude/skill_usage.json 中。"
                        ))
                    except Exception:
                        pass
                elif arg == "all":
                    threading.Thread(target=handle_skill_list, args=(chat_id, pname, None, 1, True), daemon=True).start()
                elif arg.isdigit():
                    threading.Thread(target=handle_skill_list, args=(chat_id, pname, None, int(arg)), daemon=True).start()
                elif arg:
                    threading.Thread(target=handle_skill_list, args=(chat_id, pname, arg), daemon=True).start()
                else:
                    threading.Thread(target=handle_skill_list, args=(chat_id, pname), daemon=True).start()
        elif cmd.startswith("/s+"):
            pname = _session_mgr.get_current_project(chat_id)
            sname = cmd[3:].strip()
            if sname and pname:
                threading.Thread(target=handle_skill_lock, args=(chat_id, pname, sname), daemon=True).start()
            elif not pname:
                try:
                    send_reply(chat_id, "⚠️ 请先用 /p:名称 创建项目")
                except Exception:
                    pass
        elif cmd == "/s-":
            pname = _session_mgr.get_current_project(chat_id)
            if pname:
                threading.Thread(target=handle_skill_unlock, args=(chat_id, pname), daemon=True).start()
        elif cmd == "/s?":
            pname = _session_mgr.get_current_project(chat_id)
            if pname:
                threading.Thread(target=handle_skill_status, args=(chat_id, pname), daemon=True).start()
        elif cmd.startswith("/s:"):
            pname = _session_mgr.get_current_project(chat_id)
            sname = cmd[3:].strip()
            if sname and pname:
                _skill_mgr.record_use(pname, sname.split()[0])
                threading.Thread(target=handle_message, args=(chat_id, f"/{sname}"), daemon=True).start()
            elif not pname:
                try:
                    send_reply(chat_id, "⚠️ 请先用 /p:名称 创建项目")
                except Exception:
                    pass
        elif cmd.startswith("/p:"):
            pname = cmd[3:].strip()
            if pname:
                threading.Thread(target=handle_project_switch, args=(chat_id, pname), daemon=True).start()
            else:
                threading.Thread(target=handle_project_list, args=(chat_id,), daemon=True).start()
        else:
            threading.Thread(target=handle_message, args=(chat_id, content), daemon=True).start()


if __name__ == "__main__":
    main()
