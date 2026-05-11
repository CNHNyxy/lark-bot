"""配置加载。"""

import os
import yaml
from pathlib import Path
from copy import deepcopy


DEFAULT_CONFIG = {
    "projects_root": "~/lark-projects",
    "sessions_file": "~/.lark-bot/sessions.json",
    "log_dir": "~/.lark-bot/logs",
    "log_file": "~/.lark-bot/logs/bot.log",
    "claude": {
        "model": "claude-sonnet-4-6",
        "timeout": 300,
    },
    "lark": {},
    "skills": {
        "global_dir": "~/.claude/skills",
    },
    "daemon": {
        "restart_delay": 5,
    },
}

CONFIG_SEARCH_PATHS = [
    Path("./lark-bot.yaml"),
    Path("~/.lark-bot/config.yaml").expanduser(),
]


def load_config(config_path: str | None = None) -> dict:
    """加载配置文件，合并默认值。"""
    cfg = deepcopy(DEFAULT_CONFIG)

    if config_path:
        path = Path(config_path).expanduser()
        if path.exists():
            with open(path) as f:
                user_cfg = yaml.safe_load(f) or {}
            _deep_merge(cfg, user_cfg)
    else:
        for path in CONFIG_SEARCH_PATHS:
            if path.exists():
                with open(path) as f:
                    user_cfg = yaml.safe_load(f) or {}
                _deep_merge(cfg, user_cfg)
                break

    # 环境变量覆盖
    if os.environ.get("LARK_BOT_PROJECTS_ROOT"):
        cfg["projects_root"] = os.environ["LARK_BOT_PROJECTS_ROOT"]
    if os.environ.get("LARK_BOT_SESSIONS_FILE"):
        cfg["sessions_file"] = os.environ["LARK_BOT_SESSIONS_FILE"]

    return cfg


def _deep_merge(base: dict, override: dict):
    """递归合并字典，override 覆盖 base。"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def expand_path(path: str) -> str:
    """展开 ~ 和环境变量。"""
    return os.path.expanduser(os.path.expandvars(path))
