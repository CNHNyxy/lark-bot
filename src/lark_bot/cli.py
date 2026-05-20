"""CLI 入口：启动/状态/切换/列表命令 + 进程守护。"""

import sys
import os
import io
import subprocess
import time
import signal
import datetime
import click
from pathlib import Path

from .config import load_config, expand_path


@click.group()
@click.version_option(package_name="lark-bot")
def main():
    """Lark Bot - Claude-powered Lark IM assistant."""
    pass


@main.command()
@click.option("--config", "-c", "config_path", help="配置文件路径")
@click.option("--no-daemon", is_flag=True, help="单次运行（不自动重启）")
def start(config_path, no_daemon):
    """启动 bot 服务（自带进程守护）。"""
    cfg = load_config(config_path)
    _run_daemon(cfg, no_daemon)


@main.command()
def status():
    """查看服务状态。"""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "lark-bot"],
            capture_output=True, text=True,
        )
        svc = r.stdout.strip()
        print(f"{'🟢' if svc == 'active' else '🔴'} 服务：{svc}")
    except FileNotFoundError:
        # 没有 systemctl（非 Linux），尝试检查进程
        import psutil
        running = any(
            "lark-bot" in " ".join(p.cmdline() or [])
            for p in psutil.process_iter(["cmdline"])
        )
        print(f"{'🟢 lark-bot 正在运行' if running else '🔴 lark-bot 未运行'}")
    except Exception as e:
        print(f"❓ 无法检测状态：{e}")


@main.command("list-projects")
@click.option("--chat-id", help="指定 chat_id")
def list_projects(chat_id):
    """列出所有项目。"""
    from .config import load_config
    from .session import SessionManager
    from .project import ProjectManager

    cfg = load_config()
    sm = SessionManager(cfg)
    pm = ProjectManager(cfg)

    if chat_id:
        projects = sm.get_projects(chat_id)
        current = sm.get_current_project(chat_id)
    else:
        # 显示所有 session 的所有项目
        projects = set()
        current = None
        for cid, entry in sm.sessions.items():
            for p in entry.get("projects", []):
                projects.add(p)
        projects = sorted(projects)

    if not projects:
        click.echo("📂 暂无项目")
        return

    for name in projects:
        marker = "▶ " if name == current else "  "
        tag = "（有历史）" if pm.has_session(name) else "（新）"
        click.echo(f"{marker}{name} {tag}")


@main.command("list-skills")
@click.option("--project", "project_name", help="指定项目")
def list_skills(project_name):
    """列出可用 skills。"""
    from .config import load_config
    from .skills import SkillManager
    from .project import ProjectManager

    cfg = load_config()
    pm = ProjectManager(cfg)
    sm = SkillManager(cfg, pm)

    for name, desc in sm.list_all(project_name):
        click.echo(f"{name}: {desc}")


@main.command()
@click.argument("project_name")
@click.option("--chat-id", required=True, help="指定 chat_id")
def switch(project_name, chat_id):
    """切换当前项目。"""
    from .config import load_config
    from .session import SessionManager
    from .project import ProjectManager

    cfg = load_config()
    sm = SessionManager(cfg)
    pm = ProjectManager(cfg)

    pm.ensure_dir(project_name)
    sm.set_current_project(chat_id, project_name)
    click.echo(f"✅ 已切换到项目：{project_name}")


@main.command("doctor")
def doctor():
    """检查所有依赖是否就绪。"""
    import shutil

    ok = 0
    fail = 0

    # --- Python 包 ---
    click.echo("📦 Python 包：")
    for mod, pkg in [("yaml", "pyyaml"), ("click", "click")]:
        try:
            __import__(mod)
            click.echo(f"  ✅ {pkg}")
            ok += 1
        except ImportError:
            click.echo(f"  ❌ {pkg}（pip install {pkg}）")
            fail += 1

    # --- 外部工具 ---
    click.echo("\n🔧 外部工具：")
    for tool, hint in [
        ("lark-cli", "npm install -g @larksuite/cli"),
        ("claude", "见 https://docs.anthropic.com/en/docs/claude-code"),
    ]:
        path = shutil.which(tool)
        if path:
            r = subprocess.run([tool, "--version"], capture_output=True, text=True)
            ver = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
            click.echo(f"  ✅ {tool} {ver}")
            ok += 1
        else:
            click.echo(f"  ❌ {tool} 未安装（{hint}）")
            fail += 1

    # --- lark-cli 认证 ---
    click.echo("\n🔑 lark-cli 认证：")
    lark_dirs = [
        Path.home() / ".config" / "lark-cli",
        Path.home() / ".lark-cli",
    ]
    cfg_found = any((d / "config.json").exists() for d in lark_dirs)
    if cfg_found:
        cfg_dir = next(d for d in lark_dirs if (d / "config.json").exists())
        click.echo(f"  ✅ 配置文件存在（{cfg_dir}）")
        # 验证能否实际调用
        r = subprocess.run(
            ["lark-cli", "config", "list"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            click.echo("  ✅ lark-cli 认证正常")
            ok += 2
        else:
            click.echo("  ❌ lark-cli 认证失败（运行 lark-cli auth login）")
            ok += 1
            fail += 1
    else:
        click.echo("  ❌ 未找到 lark-cli 配置（运行 lark-cli config init）")
        fail += 1

    # --- lark-bot 配置 ---
    click.echo("\n⚙️  lark-bot 配置：")
    config_paths = [
        Path("./lark-bot.yaml"),
        Path.home() / ".lark-bot" / "config.yaml",
        Path.home() / ".config" / "lark-bot" / "config.yaml",
    ]
    found_config = False
    for p in config_paths:
        if p.exists():
            click.echo(f"  ✅ {p}")
            found_config = True
            ok += 1
            break
    if not found_config:
        click.echo("  ⚠️  未找到配置文件（将使用默认配置）")
        ok += 1

    # --- 运行时目录 ---
    click.echo("\n📁 运行时目录：")
    from .config import load_config, expand_path
    bot_cfg = load_config()
    for key, label in [
        ("projects_root", "项目目录"),
        ("sessions_file", "会话文件"),
        ("log_dir", "日志目录"),
    ]:
        path = Path(expand_path(bot_cfg[key]))
        if path.exists():
            click.echo(f"  ✅ {label}：{path}")
            ok += 1
        else:
            click.echo(f"  ⚠️  {label}：{path}（首次运行时自动创建）")
            ok += 1

    # --- systemd 服务 ---
    click.echo("\n🔁 systemd 服务：")
    svc_file = Path.home() / ".config" / "systemd" / "user" / "lark-bot.service"
    old_svc = Path.home() / ".config" / "systemd" / "user" / "lark-event-consumer.service"
    if svc_file.exists():
        r = subprocess.run(["systemctl", "--user", "is-active", "lark-bot"],
                          capture_output=True, text=True)
        state = r.stdout.strip()
        click.echo(f"  ✅ lark-bot.service（{state}）")
        ok += 1
    else:
        click.echo("  ℹ️  未安装 systemd 服务（运行 lark-bot install-service）")
        ok += 1
    if old_svc.exists():
        r = subprocess.run(["systemctl", "--user", "is-active", "lark-event-consumer"],
                          capture_output=True, text=True)
        state = r.stdout.strip()
        click.echo(f"  ⚠️  发现旧服务 lark-event-consumer（{state}），建议迁移到 lark-bot")
        ok += 1

    # --- 汇总 ---
    click.echo(f"\n{'✅' if fail == 0 else '❌'} 检查完成：{ok} 通过，{fail} 失败")
    sys.exit(1 if fail else 0)


@main.command("install-service")
def install_service():
    """安装 systemd user service。"""
    import shutil

    # 生成配置文件
    config_dir = Path.home() / ".lark-bot"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    if not config_file.exists():
        example = Path(__file__).parent.parent.parent / "config.example.yaml"
        if example.exists():
            shutil.copy(example, config_file)
            click.echo(f"已创建 {config_file}，请检查配置")
        else:
            click.echo(f"未找到 config.example.yaml，请手动创建 {config_file}")

    # 安装 systemd service
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_file = systemd_dir / "lark-bot.service"

    # 自动检测 npm-global 路径用于 PATH
    npm_global = Path.home() / ".npm-global" / "bin"
    path_line = ""
    if npm_global.exists():
        path_line = f"Environment=PATH={npm_global}:%h/.local/bin:/usr/local/bin:/usr/bin:/bin"

    service_content = f"""[Unit]
Description=Lark Bot - Claude-powered IM assistant
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/lark-bot start
{path_line}
Restart=always
RestartSec=5s

[Install]
WantedBy=default.target
"""
    service_file.write_text(service_content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "lark-bot"], check=True)
    click.echo("服务已安装。运行: systemctl --user start lark-bot")


@main.command("uninstall-service")
@click.option("--purge", is_flag=True, help="同时删除配置和数据文件")
def uninstall_service(purge):
    """卸载 systemd user service。"""
    service_file = Path.home() / ".config" / "systemd" / "user" / "lark-bot.service"

    # 1. 停止并禁用服务
    if service_file.exists():
        subprocess.run(["systemctl", "--user", "stop", "lark-bot"], capture_output=True)
        subprocess.run(["systemctl", "--user", "disable", "lark-bot"], capture_output=True)
        service_file.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        click.echo("✅ 已停止并删除 systemd service")
    else:
        click.echo("ℹ️  service 文件不存在，跳过")

    # 2. 可选：清理配置和数据
    if purge:
        data_dir = Path.home() / ".lark-bot"
        if data_dir.exists():
            import shutil
            shutil.rmtree(data_dir)
            click.echo(f"🗑️  已删除 {data_dir}")
        click.echo("✅ 配置和数据已清理")
    else:
        click.echo("ℹ️  配置和数据保留（加 --purge 可一并删除）")


@main.command("export")
@click.option("--output", "-o", default="lark-bot-backup.tar.gz", help="输出文件路径")
def export_backup(output):
    """导出所有数据为压缩包（用于系统间迁移）。"""
    import tarfile
    import json

    output_path = Path(output).expanduser()

    # 从配置读取路径
    cfg = load_config()
    projects_root = Path(expand_path(cfg["projects_root"]))
    lark_bot_dir = Path.home() / ".lark-bot"
    lark_cli_dir = Path.home() / ".lark-cli"

    # 创建 manifest
    manifest = {
        "version": "0.1.0",
        "exported_at": datetime.datetime.now().isoformat(),
        "paths": {
            "projects_root": str(projects_root),
            "lark_bot_dir": str(lark_bot_dir),
        }
    }

    with tarfile.open(output_path, "w:gz") as tar:
        # 添加 manifest
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        tar.addfile(manifest_info, io.BytesIO(manifest_bytes))

        # 添加 ~/.lark-bot/
        if lark_bot_dir.exists():
            for p in lark_bot_dir.rglob("*"):
                if p.is_file():
                    tar.add(p, arcname=f"lark-bot/{p.relative_to(lark_bot_dir)}")
            click.echo(f"📦 已打包 {lark_bot_dir}")

        # 添加 ~/lark-projects/
        if projects_root.exists():
            for p in projects_root.rglob("*"):
                if p.is_file():
                    tar.add(p, arcname=f"lark-projects/{p.relative_to(projects_root)}")
            click.echo(f"📦 已打包 {projects_root}")

        # 添加 ~/.lark-cli/（如存在）
        if lark_cli_dir.exists():
            for p in lark_cli_dir.rglob("*"):
                if p.is_file():
                    tar.add(p, arcname=f"lark-cli/{p.relative_to(lark_cli_dir)}")
            click.echo(f"📦 已打包 {lark_cli_dir}")

    click.echo(f"✅ 导出完成：{output_path}")
    click.echo(f"   大小：{output_path.stat().st_size // 1024 // 1024} MB")


@main.command("import")
@click.argument("backup_file")
def import_backup(backup_file):
    """从压缩包导入数据。"""
    import tarfile
    import json

    backup_path = Path(backup_file).expanduser()
    if not backup_path.exists():
        click.echo(f"❌ 文件不存在：{backup_path}")
        return

    # 从当前配置读取路径
    cfg = load_config()
    projects_root = Path(expand_path(cfg["projects_root"]))
    lark_bot_dir = Path.home() / ".lark-bot"
    lark_cli_dir = Path.home() / ".lark-cli"

    # 创建目录
    lark_bot_dir.mkdir(parents=True, exist_ok=True)
    projects_root.mkdir(parents=True, exist_ok=True)

    with tarfile.open(backup_path, "r:gz") as tar:
        # 读取 manifest
        try:
            manifest_member = tar.getmember("manifest.json")
            manifest_file = tar.extractfile(manifest_member)
            manifest = json.loads(manifest_file.read())
            click.echo(f"📋 备份版本：{manifest.get('version', 'unknown')}")
            click.echo(f"📋 导出时间：{manifest.get('exported_at', 'unknown')}")
        except KeyError:
            click.echo("⚠️  未找到 manifest.json，继续导入...")

        # 解压文件
        for member in tar.getmembers():
            if member.name.startswith("lark-bot/"):
                # 解压到 ~/.lark-bot/
                target = lark_bot_dir / member.name[10:]  # 去掉 "lark-bot/" 前缀
            elif member.name.startswith("lark-projects/"):
                # 解压到 ~/lark-projects/
                target = projects_root / member.name[15:]  # 去掉 "lark-projects/" 前缀
            elif member.name.startswith("lark-cli/"):
                # 解压到 ~/.lark-cli/
                target = lark_cli_dir / member.name[10:]  # 去掉 "lark-cli/" 前缀
            else:
                continue

            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src:
                    with open(target, "wb") as dst:
                        dst.write(src.read())

    click.echo(f"✅ 导入完成")
    click.echo(f"   lark-bot 数据：{lark_bot_dir}")
    click.echo(f"   项目目录：{projects_root}")
    if lark_cli_dir.exists():
        click.echo(f"   lark-cli 配置：{lark_cli_dir}")
    click.echo("\n运行以下命令验证：")
    click.echo("  lark-bot doctor")


def _run_daemon(cfg: dict, no_daemon: bool):
    """守护循环：启动 lark-cli 管道 + bot core，崩溃自动重启。"""
    log_dir = Path(expand_path(cfg.get("log_dir", "~/.lark-bot/logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot.log"

    restart_delay = cfg.get("daemon", {}).get("restart_delay", 5)
    profile = cfg.get("profile")
    child_env = os.environ.copy()
    if cfg.get("_config_path"):
        child_env["LARK_BOT_CONFIG"] = cfg["_config_path"]

    lark_proc = None
    bot_proc = None
    stdin_pipe_w = None

    try:
        while True:
            click.echo("[lark-bot] 启动服务...")

            try:
                # lark-cli 把 stdin EOF 视为退出信号，必须保持 stdin 打开
                # 用管道代替 DEVNULL，写入端不关闭即可保持打开
                stdin_pipe_r, stdin_pipe_w = os.pipe()
                stdin_pipe = os.fdopen(stdin_pipe_r, "r")

                # 启动 lark-cli 事件流
                lark_cmd = ["lark-cli"]
                if profile:
                    lark_cmd += ["--profile", profile]
                lark_cmd += ["event", "consume", "im.message.receive_v1",
                             "--as", "bot", "--max-events", "0"]
                lark_proc = subprocess.Popen(
                    lark_cmd,
                    stdin=stdin_pipe,
                    stdout=subprocess.PIPE,
                    stderr=log_file.open("a"),
                    text=True,
                    env=child_env,
                )
                stdin_pipe.close()  # 子进程已继承，关闭父进程的读取端
                # stdin_pipe_w 不关闭，保持 stdin 打开，防止 lark-cli 退出

                # 启动 bot core（从 stdin 读取事件）
                bot_proc = subprocess.Popen(
                    [sys.executable, "-m", "lark_bot.bot"],
                    stdin=lark_proc.stdout,
                    stdout=log_file.open("a"),
                    stderr=log_file.open("a"),
                    text=True,
                    env=child_env,
                )
                # 关闭 lark_proc.stdout 的引用，让 bot_proc 能收到 EOF
                lark_proc.stdout.close()

                # 等待任意进程退出
                while True:
                    if lark_proc.poll() is not None:
                        click.echo(f"[lark-bot] lark-cli 退出 (code={lark_proc.returncode})")
                        bot_proc.terminate()
                        break
                    if bot_proc.poll() is not None:
                        click.echo(f"[lark-bot] bot core 退出 (code={bot_proc.returncode})")
                        lark_proc.terminate()
                        break
                    time.sleep(1)

            except KeyboardInterrupt:
                click.echo("\n[lark-bot] 收到中断信号，退出...")
                break
            except Exception as e:
                click.echo(f"[lark-bot] 异常: {e}")

            # 清理本次进程和管道
            for proc in (lark_proc, bot_proc):
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            if stdin_pipe_w is not None:
                os.close(stdin_pipe_w)
                stdin_pipe_w = None

            if no_daemon:
                break

            click.echo(f"[lark-bot] {restart_delay}秒后重启...")
            time.sleep(restart_delay)

    finally:
        # 清理进程
        for proc in (lark_proc, bot_proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if stdin_pipe_w is not None:
            os.close(stdin_pipe_w)
