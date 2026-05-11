"""项目管理：项目目录、session 检测、状态查询。"""

import os
import datetime
from pathlib import Path
from .config import expand_path


class ProjectManager:
    def __init__(self, config: dict):
        self.projects_root = Path(expand_path(config["projects_root"]))

    def project_dir(self, name: str) -> str:
        return str(self.projects_root / name)

    def ensure_dir(self, name: str) -> str:
        pdir = self.project_dir(name)
        os.makedirs(pdir, exist_ok=True)
        return pdir

    def has_session(self, name: str) -> bool:
        pdir = self.project_dir(name)
        mapped = pdir.replace("/", "-")
        claude_dir = Path.home() / ".claude" / "projects" / mapped
        if not claude_dir.is_dir():
            return False
        return any(f.endswith(".jsonl") for f in os.listdir(claude_dir))

    def get_session_info(self, name: str) -> dict:
        pdir = self.project_dir(name)
        mapped = pdir.replace("/", "-")
        claude_dir = Path.home() / ".claude" / "projects" / mapped
        if not claude_dir.is_dir():
            return {}
        jsonl_files = [
            str(p) for p in claude_dir.iterdir()
            if p.suffix == ".jsonl" and not p.name.startswith(".")
        ]
        if not jsonl_files:
            return {}
        latest = max(jsonl_files, key=os.path.getmtime)
        st = os.stat(latest)
        with open(latest) as f:
            lines = sum(1 for _ in f)
        return {
            "session_id": Path(latest).stem[:8],
            "size_kb": st.st_size // 1024,
            "lines": lines,
            "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
        }
