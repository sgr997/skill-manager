"""上游来源与本地改动判定。

三套来源记录：
  1. ~/.hermes/skills/.hub/lock.json   hermes skills install 装的，带 content_hash（权威）
  2. ~/.agents/.skill-lock.json       skills.sh 装的，带 sourceUrl 但 hash 算法与本地不同
  3. data/baseline.json               我们自己记的基线，补上面第 2 类算不出 hash 的缺口

判定「本地改过」的顺序：
  hub 来源 → 直接比 lock 里的 content_hash（Hermes 自己的算法，import 它的函数保证同步）
  其它来源 → 比 baseline 里记的第一次见到的 hash
  两边都没有 → 记为 unknown，更新按钮照常给，但在页面上标出来
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from . import config as cfg_mod

HERMES_HOME = cfg_mod.path("paths", "hermes_home")
SKILLS = HERMES_HOME / "skills"
HUB_LOCK = SKILLS / ".hub" / "lock.json"
AGENTS_LOCK = Path.home() / ".agents" / ".skill-lock.json"
AGENTS_SKILLS = Path.home() / ".agents" / "skills"
DATA = Path(__file__).resolve().parent.parent / "data"
BASELINE = DATA / "baseline.json"


def _content_hash(path: Path) -> str:
    """用 Hermes 自己的算法。import 失败时用同一套逻辑的本地副本（见 tools/skills_guard.py:763）。"""
    try:
        if str(HERMES_HOME / "hermes-agent") not in sys.path:
            sys.path.insert(0, str(HERMES_HOME / "hermes-agent"))
        from tools.skills_guard import content_hash  # type: ignore
        return content_hash(path)
    except Exception:
        h = hashlib.sha256()
        if path.is_dir():
            pairs = sorted((p.relative_to(path).as_posix(), p) for p in path.rglob("*") if p.is_file())
            for rel, p in pairs:
                h.update(rel.encode("utf-8") + b"\x00")
                try:
                    h.update(p.read_bytes())
                except OSError:
                    pass
        else:
            h.update(path.read_bytes())
        return f"sha256:{h.hexdigest()[:16]}"


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def upstream_index() -> dict[str, dict]:
    """name → {kind, identifier, url, upstream, recordedHash, installedAt}"""
    out: dict[str, dict] = {}

    for name, rec in (_load(HUB_LOCK).get("installed") or {}).items():
        out[name] = {
            "kind": "hermes-hub",
            "identifier": rec.get("identifier", ""),
            "url": "",
            "upstream": rec.get("source", "official"),
            "recordedHash": (rec.get("content_hash") or "").replace("sha256:", ""),
            "installPath": rec.get("install_path") or name,
            "installedAt": "",
        }

    for name, rec in (_load(AGENTS_LOCK).get("skills") or {}).items():
        if name in out:
            continue
        out[name] = {
            "kind": "skills-sh",
            "identifier": rec.get("skillPath") or "",
            "url": rec.get("sourceUrl", ""),
            "upstream": rec.get("source", ""),
            "recordedHash": "",
            "installPath": name,
            "installedAt": (rec.get("installedAt") or "")[:10],
        }
    return out


def baseline() -> dict:
    return _load(BASELINE)


def check(name: str, path: Path, upstream: dict | None) -> dict:
    """返回 {status: clean|modified|unknown, hash, since}

    clean    = 和记录一致，可以安全更新
    modified = 本地有改动，更新会覆盖，页面标红
    unknown  = 第一次见，还没基线；本次写入基线，状态按 unknown 报
    """
    cur = _content_hash(path)
    base = baseline()
    known = base.get(name) or {}

    if upstream and upstream.get("recordedHash"):
        ok = cur.replace("sha256:", "") == upstream["recordedHash"]
        return {"status": "clean" if ok else "modified", "hash": cur,
                "since": known.get("recorded") or upstream.get("installedAt") or ""}

    if known.get("hash"):
        ok = cur == known["hash"]
        return {"status": "clean" if ok else "modified", "hash": cur,
                "since": known.get("recorded", "")}

    base[name] = {"hash": cur, "recorded": time.strftime("%Y-%m-%d %H:%M")}
    _save(BASELINE, base)
    return {"status": "unknown", "hash": cur, "since": base[name]["recorded"]}


def rebaseline(name: str, path: Path) -> dict:
    """更新成功后把基线刷成当前内容，否则下一次扫描会把它当成「本地改过」。"""
    cur = _content_hash(path)
    base = baseline()
    base[name] = {"hash": cur, "recorded": time.strftime("%Y-%m-%d %H:%M")}
    _save(BASELINE, base)
    return {"hash": cur}


def orphans() -> list[str]:
    """上游已删、本地还在的 skill。判断依据：装了但不在最近一次 check 的上游清单里。"""
    known = _load(DATA / "upstream-gone.json")
    return sorted(known.get("names") or [])


if __name__ == "__main__":
    idx = upstream_index()
    print(f"上游记录 {len(idx)} 条")
    from collections import Counter
    for k, v in Counter(v["kind"] for v in idx.values()).most_common():
        print(f"  {v:>3}  {k}")
    print("\n前 5 条:")
    for name in list(idx)[:5]:
        print(f"  {name:34s} {idx[name]['kind']:<12} {idx[name]['identifier'][:44]}")
