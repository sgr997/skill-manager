"""扫描 Hermes 的 skill 目录，产出结构化清单。

只读，不改任何东西。输出给 API 层用。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import config as cfg_mod

HERMES = cfg_mod.path("paths", "hermes_home")
SKILLS = HERMES / "skills"
USAGE = cfg_mod.path("paths", "usage_file")
HUB_LOCK = cfg_mod.path("sources", "hermes_hub_lock")
AGENTS_LOCK = cfg_mod.path("sources", "skills_sh_lock")
AGENTS_SKILLS = Path.home() / ".agents" / "skills"

EXCLUDED = ("/.archive/", "/.backup", ".curator_backups", "/.hub/", "/node_modules/")
TEXTY = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh", ".js", ".ts",
         ".tsx", ".jsx", ".toml", ".cfg", ".ini", ".css", ".html", ".xml", ".csv"}


def estimate_tokens(text: str) -> int:
    """按 CJK / 其它字符分别估。主流分词器：中文约 1 字 1 token，英文符号约 3.5 字符 1 token。

    比 len()/4 那种一律按英文算的做法准得多 —— 我们这堆 description 中英混排，
    纯英文公式会把中文严重低估。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    other = len(text) - cjk
    return int(round(cjk * 1.0 + other / 3.5))


def _frontmatter(text: str) -> dict:
    """解析 SKILL.md 的 frontmatter。

    走 yaml.safe_load 而不是逐行正则：`description: >` 和 `description: |` 的正文在缩进的
    后续行里，逐行正则只能拿到那个 `>` 或 `|` 符号，描述就整条丢了。
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    raw = text[3:end]
    try:
        import yaml
        data = yaml.safe_load(raw) or {}
        if isinstance(data, dict):
            return {str(k): ("" if v is None else str(v)) for k, v in data.items()}
    except Exception:
        pass
    # yaml 不可用或解析失败时退到逐行，至少拿到单行值
    out: dict[str, str] = {}
    for line in raw.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.+)$", line)
        if m:
            val = m.group(2).strip().strip('"').strip("'")
            if val not in (">", "|", ">-", "|-", ">+", "|+"):
                out[m.group(1)] = val
    return out


def _walk(d: Path) -> tuple[int, int, int, int]:
    """返回 文件数、总字节、references 里的 md 数、最大单文件字节。"""
    files = total = refs = biggest = 0
    for root, _, names in os.walk(d, followlinks=False):
        for f in names:
            fp = os.path.join(root, f)
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            files += 1
            total += size
            biggest = max(biggest, size)
            if os.sep + "references" + os.sep in fp and f.endswith(".md"):
                refs += 1
    return files, total, refs, biggest


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _disabled_config() -> tuple[set[str], dict[str, list[str]]]:
    """从 config.yaml 读 skills.disabled 与 skills.platform_disabled。"""
    try:
        import yaml
    except ImportError:
        return set(), {}
    try:
        cfg = yaml.safe_load((HERMES / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, Exception):
        return set(), {}
    sk = cfg.get("skills") or {}
    glob = set(sk.get("disabled") or [])
    per = {k: list(v or []) for k, v in (sk.get("platform_disabled") or {}).items()}
    return glob, per


def _session_calls() -> dict[str, dict]:
    """从 state.db 统计真实 skill_view 调用。"""
    import collections
    import sqlite3

    db = HERMES / "state.db"
    if not db.exists():
        return {}
    out: dict[str, dict] = collections.defaultdict(lambda: {"n": 0, "last": 0})
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT timestamp, tool_calls FROM messages WHERE tool_calls LIKE '%skill_view%'"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    for ts, blob in rows:
        try:
            arr = json.loads(blob)
        except (TypeError, ValueError):
            continue
        if not isinstance(arr, list):
            continue
        for call in arr:
            fn = call.get("function") or {}
            if fn.get("name") != "skill_view":
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            name = (args.get("name") or "").split(":")[-1].strip()
            if name:
                out[name]["n"] += 1
                out[name]["last"] = max(out[name]["last"], ts or 0)
    return out


def _sources() -> dict[str, dict]:
    """合并两套来源记录，键是 skill 名。"""
    out: dict[str, dict] = {}

    hub = _load_json(HUB_LOCK).get("installed") or {}
    for name, rec in hub.items():
        out[name] = {
            "kind": "hermes-hub",
            "identifier": rec.get("identifier", ""),
            "url": "",
            "upstream": rec.get("source", "official"),
            "hash": (rec.get("content_hash") or "").replace("sha256:", ""),
            "files": rec.get("files") or [],
            "installedAt": "",
        }

    sh = _load_json(AGENTS_LOCK).get("skills") or {}
    for name, rec in sh.items():
        if name in out:
            continue
        out[name] = {
            "kind": "skills-sh",
            "identifier": rec.get("skillPath") or "",
            "url": rec.get("sourceUrl", ""),
            "upstream": rec.get("source", ""),
            "hash": rec.get("skillFolderHash") or "",
            "files": [],
            "installedAt": (rec.get("installedAt") or "")[:10],
        }
    return out


def _all_skill_dirs() -> list[Path]:
    """按配置的多个根目录依次扫，用 realpath 去重。

    skill_roots 配多个时（比如自建目录 + 外部目录），同一个实体可能通过软链出现两次，
    去重后才不会重复列出。
    """
    seen: set[str] = set()
    out: list[Path] = []
    for root in cfg_mod.skill_roots():
        for d in _skill_dirs(root):
            key = os.path.realpath(d)
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
    return out


def _skill_dirs(base: Path | None = None, depth: int = 0) -> list[Path]:
    """递归枚举 skill 目录。

    分类目录自己可能是 skill，同时也装着子 skill，两者都收。
    软链只在它本身就含 SKILL.md 时收，不往里走 —— 否则会扫到 ~/.agents 里没挂载的那 38 个。
    """
    out: list[Path] = []
    root = base or SKILLS
    try:
        if not root.is_dir():
            return out
    except OSError:
        return out
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.name.startswith(".") or entry.name in ("node_modules", "references", "scripts",
                                                        "templates", "assets", "data"):
            continue
        try:
            is_link = entry.is_symlink()
            has_skill = (entry / "SKILL.md").is_file()
            is_dir = entry.is_dir()
        except OSError:
            # 权限受限的目录（~/Library 之类）跳过，不能让一处 stat 失败拖垮整个扫描
            continue
        if is_link:
            if has_skill:
                out.append(entry)
            continue
        if not is_dir:
            continue
        if has_skill:
            out.append(entry)
        if depth < 3:
            out.extend(_skill_dirs(entry, depth + 1))
    return out


def scan() -> list[dict]:
    usage = _load_json(USAGE)
    sess = _session_calls()
    srcs = _sources()
    glob_disabled, per_disabled = _disabled_config()

    rows: list[dict] = []
    seen: set[str] = set()
    for d in _all_skill_dirs():
        path = d / "SKILL.md"
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm = _frontmatter(text)
        name = fm.get("name") or d.name
        # 同名 skill 可能在多个根目录里各有一份（一份是转发桩、一份是完整版），
        # 按配置顺序保留先出现的那个
        if name in seen:
            continue
        seen.add(name)
        files, total, refs, biggest = _walk(d)
        # 配了多个根目录时 d 不一定在 SKILLS 下面，找不到就用相对各根的路径
        rel = None
        for root in cfg_mod.skill_roots():
            try:
                rel = str(d.relative_to(root))
                break
            except ValueError:
                continue
        if rel is None:
            try:
                rel = str(d.relative_to(SKILLS))
            except ValueError:
                rel = d.name
        rec = usage.get(name) or {}
        sv = sess.get(name, {"n": 0, "last": 0})
        src = srcs.get(name)

        rows.append({
            "name": name,
            "path": rel,
            "abs": str(d),
            "cat": rel.split("/")[0] if "/" in rel else "",
            "desc": fm.get("description", ""),
            "size": total,
            "files": files,
            "refs": refs,
            "biggest": biggest,
            "link": d.is_symlink(),
            "target": os.readlink(d) if d.is_symlink() else "",
            # readlink 给的是相对路径（../../.agents/skills/x），展示用真实绝对路径
            "realPath": os.path.realpath(d),
            "source": src,
            "use": max(int(rec.get("use_count") or 0), sv["n"]),
            "useCurator": int(rec.get("use_count") or 0),
            "useSessions": sv["n"],
            "patches": int(rec.get("patch_count") or 0),
            "state": rec.get("state") or "",
            "pinned": bool(rec.get("pinned")),
            "lastUsed": (rec.get("last_used_at") or "")[:10],
            "disabled": name in glob_disabled,
            "disabledPlatforms": [p for p, lst in per_disabled.items() if name in lst],
            "mtime": int(d.joinpath("SKILL.md").stat().st_mtime),
            # 系统提示里注入的是「名字 + 描述」这一行，不算正文
            "ctxChars": len(name) + len(fm.get("description", "")) + 4,
            "ctxTokens": estimate_tokens(name + ": " + fm.get("description", "")),
        })

    rows.sort(key=lambda r: (-r["size"], r["name"]))
    return rows


def summary(rows: list[dict]) -> dict:
    active = [r for r in rows if not r["disabled"]]
    return {
        "total": len(rows),
        "bytes": sum(r["size"] for r in rows),
        "disabled": len(rows) - len(active),
        "linked": sum(1 for r in rows if r["link"]),
        "upstream": sum(1 for r in rows if r["source"]),
        # 已启用的 skill 每轮都随系统提示注入，这部分是常驻开销
        "ctxTokens": sum(r["ctxTokens"] for r in active),
        "ctxChars": sum(r["ctxChars"] for r in active),
        "savedTokens": sum(r["ctxTokens"] for r in rows if r["disabled"]),
        "generated": time.strftime("%Y-%m-%d %H:%M"),
    }


if __name__ == "__main__":
    data = scan()
    s = summary(data)
    print(json.dumps(s, ensure_ascii=False, indent=1))
    print(f"\n上游来源分布:")
    from collections import Counter
    for k, v in Counter((r["source"] or {}).get("kind", "本地") for r in data).most_common():
        print(f"  {v:>3}  {k}")
    print(f"\n前 5 大:")
    for r in data[:5]:
        print(f"  {r['size']/1048576:>6.2f} MB  {r['name']:<32} {r['files']:>3} 文件 {r['refs']:>3} refs")
