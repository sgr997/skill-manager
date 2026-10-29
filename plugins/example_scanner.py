"""示例：从任意地方捞 skill 记录。

这个演示从一份 JSON 文件读，真实场景可以是：远程 API、数据库、别的 agent 的
目录（Claude、Codex、自建目录）。

返回的每条记录里 name 和 desc 必须有，其余字段能填就填，没填的框架补默认值。

挂上：config.toml 里写
    [paths]
    scanners = ["plugins.example_scanner:JsonScanner"]
"""

import json
import os
from pathlib import Path

from core.plugins import BaseScanner


class JsonScanner(BaseScanner):
    name = "json-file"

    def __init__(self, path: str | None = None, **kwargs):
        # 配置文件里没给就用默认位置
        self.path = Path(os.path.expanduser(path or "~/extra-skills.json"))

    def scan(self) -> list[dict]:
        if not self.path.is_file():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        out = []
        for item in data if isinstance(data, list) else data.get("skills", []):
            out.append({
                "name": item["name"],
                "desc": item.get("description") or item.get("desc") or "",
                "path": f"remote/{item['name']}",
                "cat": "remote",
                "size": int(item.get("size") or 0),
                "files": int(item.get("files") or 1),
                "source": "plugin",
            })
        return out
