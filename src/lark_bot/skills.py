"""Skill 发现、列表、使用记录、锁定。"""

import os
import json
import datetime
from pathlib import Path
from .config import expand_path
from .project import ProjectManager


PAGE_SIZE = 5


class SkillManager:
    def __init__(self, config: dict, project_manager: ProjectManager):
        self.global_dir = Path(expand_path(config["skills"]["global_dir"]))
        self.pm = project_manager

    def list_all(self, project_name: str | None = None) -> list[tuple[str, str]]:
        """返回 [(name, short_description)]，项目级排前面。"""
        skills = []
        seen = set()

        # 项目级 skills
        if project_name:
            proj_dir = os.path.join(self.pm.project_dir(project_name), ".claude", "skills")
            if os.path.isdir(proj_dir):
                for entry in sorted(os.listdir(proj_dir)):
                    md = os.path.join(proj_dir, entry, "SKILL.md")
                    if os.path.isfile(md):
                        desc = self._read_desc(md)
                        skills.append((entry, desc))
                        seen.add(entry)

        # 全局 skills
        if self.global_dir.is_dir():
            for entry in sorted(os.listdir(self.global_dir)):
                if entry in seen:
                    continue
                md = str(self.global_dir / entry / "SKILL.md")
                if os.path.isfile(md):
                    desc = self._read_desc(md)
                    skills.append((entry, desc))

        return skills

    def _read_desc(self, md_path: str) -> str:
        try:
            with open(md_path) as f:
                for line in f:
                    if line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                        if desc.startswith("|"):
                            desc = desc.lstrip("|").strip()
                        return desc[:37] + "..." if len(desc) > 40 else desc
        except Exception:
            pass
        return ""

    # --- 使用记录 ---

    def _usage_path(self, project_name: str) -> str:
        return os.path.join(self.pm.project_dir(project_name), ".claude", "skill_usage.json")

    def load_usage(self, project_name: str) -> dict:
        try:
            with open(self._usage_path(project_name)) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_usage(self, project_name: str, data: dict):
        path = self._usage_path(project_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def record_use(self, project_name: str, skill_name: str):
        usage = self.load_usage(project_name)
        now = datetime.datetime.now().isoformat()
        if skill_name not in usage:
            usage[skill_name] = {"last_used": now, "count": 0}
        usage[skill_name]["last_used"] = now
        usage[skill_name]["count"] += 1
        self.save_usage(project_name, usage)

    def get_locked(self, project_name: str) -> str | None:
        usage = self.load_usage(project_name)
        return usage.get("locked_skill")

    def set_locked(self, project_name: str, skill_name: str):
        usage = self.load_usage(project_name)
        usage["locked_skill"] = skill_name
        self.save_usage(project_name, usage)

    def clear_locked(self, project_name: str) -> str | None:
        usage = self.load_usage(project_name)
        locked = usage.pop("locked_skill", None)
        self.save_usage(project_name, usage)
        return locked


def relative_time(iso_str: str) -> str:
    """将 ISO 时间字符串转为相对时间描述。"""
    try:
        t = datetime.datetime.fromisoformat(iso_str)
        now = datetime.datetime.now()
        delta = now - t
        if delta.total_seconds() < 60:
            return "刚才"
        if delta.total_seconds() < 3600:
            return f"{int(delta.total_seconds() / 60)}分钟前"
        if delta.total_seconds() < 86400:
            return f"{int(delta.total_seconds() / 3600)}小时前"
        if delta.days < 7:
            return f"{delta.days}天前"
        return t.strftime("%m-%d")
    except Exception:
        return ""
