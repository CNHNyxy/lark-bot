"""智能体管理：多实例配置、systemd 服务、状态查询。"""

import os
import json
import subprocess
import shutil
from pathlib import Path

from .config import (
    LARK_BOT_HOME, load_global_config, load_agent_config,
    agent_config_file, global_config_file, expand_path,
)


class AgentManager:
    """管理 lark-bot 智能体实例。

    目录结构：
        ~/.lark-bot/
        ├── config.yaml       # 全局配置
        ├── prod/
        │   ├── config.yaml   # 智能体配置
        │   └── sessions.json
        ├── dev/
        │   └── ...
        └── logs/
            ├── prod.log
            └── dev.log
    """

    def __init__(self):
        self.home = LARK_BOT_HOME
        self.systemd_dir = Path.home() / ".config" / "systemd" / "user"
        self.template_service = self.systemd_dir / "lark-bot@.service"

    def agent_dir(self, name: str) -> Path:
        return self.home / name

    def list_agents(self) -> list[dict]:
        """扫描所有智能体（基于子目录中存在 config.yaml）。"""
        agents = []
        if not self.home.exists():
            return agents
        for entry in sorted(self.home.iterdir()):
            if entry.is_dir() and (entry / "config.yaml").exists():
                agents.append(self._get_agent_info(entry.name))
        return agents

    def _get_agent_info(self, name: str) -> dict:
        info = {
            "name": name,
            "config": str(agent_config_file(name)),
            "profile": None,
            "app_id": None,
            "projects_root": None,
            "status": "unknown",
        }

        try:
            cfg = load_agent_config(name)
            info["profile"] = cfg.get("profile")
            info["projects_root"] = expand_path(cfg.get("projects_root", ""))
        except Exception:
            pass

        if info["profile"]:
            try:
                r = subprocess.run(
                    ["lark-cli", "--profile", info["profile"], "config", "show"],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL,
                )
                if r.returncode == 0:
                    data = json.loads(r.stdout)
                    info["app_id"] = data.get("appId")
            except Exception:
                pass

        info["status"] = self.service_status(name)
        return info

    def service_status(self, name: str) -> str:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", f"lark-bot@{name}"],
                capture_output=True, text=True,
            )
            return r.stdout.strip()
        except Exception:
            return "unknown"

    def ensure_template_service(self):
        """确保 systemd 模板服务存在。"""
        if self.template_service.exists():
            return
        self.systemd_dir.mkdir(parents=True, exist_ok=True)
        npm_global = Path.home() / ".npm-global" / "bin"
        path_env = f"{npm_global}:%h/.local/bin:/usr/local/bin:/usr/bin:/bin"
        content = f"""[Unit]
Description=Lark Bot - %i instance
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/lark-bot start --agent %i
Environment=PATH={path_env}
Restart=always
RestartSec=5s

[Install]
WantedBy=default.target
"""
        self.template_service.write_text(content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)

    def ensure_global_config(self):
        """确保全局配置文件存在（首次使用时创建）。"""
        global_cfg = global_config_file()
        if global_cfg.exists():
            return
        self.home.mkdir(parents=True, exist_ok=True)
        import yaml
        content = {
            "claude": {
                "model": "claude-sonnet-4-6",
                "timeout": 300,
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "sk-your-api-key-here",
                    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
                },
            },
            "skills": {
                "global_dir": "~/.claude/skills",
            },
            "daemon": {
                "restart_delay": 5,
            },
        }
        with open(global_cfg, "w") as f:
            yaml.safe_dump(content, f, allow_unicode=True, sort_keys=False)
        print(f"✅ 已创建全局配置：{global_cfg}")
        print("   请编辑该文件填入 ANTHROPIC_AUTH_TOKEN 等配置")

    def create_agent(self, name: str, profile: str | None = None) -> dict:
        """创建新智能体。"""
        if profile is None:
            profile = name

        agent_dir = self.agent_dir(name)
        if agent_dir.exists():
            raise FileExistsError(f"智能体 {name} 已存在：{agent_dir}")

        # 1. 确保全局配置存在
        self.ensure_global_config()

        # 2. 创建 lark-cli profile
        existing_profiles = self._list_lark_profiles()
        if profile not in existing_profiles:
            print(f"📱 正在为智能体 {name} 创建飞书应用（profile: {profile}）...")
            print("⏳ 请在浏览器中完成授权...")
            subprocess.run(
                ["lark-cli", "config", "init", "--new", "--name", profile],
                check=True,
            )
        else:
            print(f"ℹ️  使用已有 lark-cli profile: {profile}")

        # 3. 创建智能体目录
        agent_dir.mkdir(parents=True, exist_ok=True)
        (self.home / "logs").mkdir(exist_ok=True)

        # 4. 生成智能体配置（只含智能体特有字段）
        import yaml
        agent_cfg = {
            "profile": profile,
            "projects_root": f"~/lark-projects-{name}" if name != "prod" else "~/lark-projects",
        }
        with open(agent_config_file(name), "w") as f:
            yaml.safe_dump(agent_cfg, f, allow_unicode=True, sort_keys=False)

        # 5. 注册 systemd 服务
        self.ensure_template_service()

        return self._get_agent_info(name)

    def _list_lark_profiles(self) -> list[str]:
        try:
            r = subprocess.run(
                ["lark-cli", "profile", "list"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
                return [p.get("name", "") for p in data if isinstance(p, dict)]
        except Exception:
            pass
        return []

    def remove_agent(self, name: str, purge: bool = False):
        subprocess.run(["systemctl", "--user", "stop", f"lark-bot@{name}"], capture_output=True)
        subprocess.run(["systemctl", "--user", "disable", f"lark-bot@{name}"], capture_output=True)
        if purge:
            agent_dir = self.agent_dir(name)
            if agent_dir.exists():
                shutil.rmtree(agent_dir)
                print(f"🗑️  已删除 {agent_dir}")

    def start(self, name: str):
        subprocess.run(["systemctl", "--user", "start", f"lark-bot@{name}"], check=True)

    def stop(self, name: str):
        subprocess.run(["systemctl", "--user", "stop", f"lark-bot@{name}"], check=True)

    def restart(self, name: str):
        subprocess.run(["systemctl", "--user", "restart", f"lark-bot@{name}"], check=True)

    def enable(self, name: str):
        subprocess.run(["systemctl", "--user", "enable", f"lark-bot@{name}"], check=True)
