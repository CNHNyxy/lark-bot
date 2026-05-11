"""Session 管理：chat_id → {current, projects} 映射。"""

import json
import threading
from pathlib import Path
from .config import expand_path


class SessionManager:
    def __init__(self, config: dict):
        self.path = Path(expand_path(config["sessions_file"]))
        self.sessions: dict = self._load()
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        # 只保留新格式
        cleaned = {}
        for cid, val in data.items():
            if isinstance(val, dict) and isinstance(val.get("projects"), list):
                cleaned[cid] = val
        return cleaned

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.sessions, f, indent=2, ensure_ascii=False)

    def get_current_project(self, chat_id: str) -> str | None:
        entry = self.sessions.get(chat_id)
        if not entry:
            return None
        return entry.get("current")

    def get_projects(self, chat_id: str) -> list[str]:
        entry = self.sessions.get(chat_id, {})
        return entry.get("projects", [])

    def set_current_project(self, chat_id: str, project_name: str):
        with self.lock:
            if chat_id not in self.sessions:
                self.sessions[chat_id] = {"current": project_name, "projects": []}
            self.sessions[chat_id]["current"] = project_name
            if project_name not in self.sessions[chat_id]["projects"]:
                self.sessions[chat_id]["projects"].append(project_name)
            self.save()

    def remove_project(self, chat_id: str, project_name: str) -> bool:
        with self.lock:
            entry = self.sessions.get(chat_id, {})
            projects = entry.get("projects", [])
            if project_name not in projects:
                return False
            projects.remove(project_name)
            was_current = entry.get("current") == project_name
            if was_current:
                entry["current"] = projects[0] if projects else None
            self.save()
            return was_current
