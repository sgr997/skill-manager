"""启用/禁用 skill —— 写 config.yaml 的 skills.disabled。

为什么走 `hermes config set` 而不是自己 dump YAML：config.yaml 里有一千多行、
大量注释和手写顺序，yaml.dump 一次就把文件结构全重排了。官方命令只改目标键。

下一轮对话生效，不用重启网关：prompt_builder 的缓存 key 里含
tuple(sorted(disabled))（agent/prompt_builder.py:1417），值一变 key 就变。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import config as cfg_mod

HERMES_HOME = cfg_mod.path("paths", "hermes_home")
CONFIG = cfg_mod.path("paths", "hermes_config")
AUDIT = cfg_mod.data_dir() / "audit.log"

# agent/skill_utils.py:276 —— 系统提示无条件指向它，禁不掉
ESSENTIAL = {"hermes-agent"}


def _config() -> dict:
    import yaml
    try:
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, Exception):
        return {}


def disabled() -> list[str]:
    skills = _config().get("skills") or {}
    return list(skills.get("disabled") or [])


def _write(names: list[str]) -> tuple[bool, str]:
    payload = json.dumps(sorted(set(names)), ensure_ascii=False)
    # 用绝对路径：服务由 launchd 拉起时 PATH 里没有 ~/.local/bin
    cmd = [cfg_mod.resolve_cmd(cfg_mod.CFG["update"].get("hermes_cmd") or "hermes"),
           "config", "set", "skills.disabled", payload, "--force"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"调用失败：{e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:400]
    after = disabled()
    if sorted(set(names)) != sorted(after):
        return False, f"写入未生效：期望 {sorted(set(names))}，实际 {after}"
    return True, "ok"


def _audit(action: str, name: str, ok: bool, detail: str = "") -> None:
    import time
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{action}\t{name}\t{'ok' if ok else 'FAIL'}\t{detail}\n"
    try:
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def set_enabled(name: str, enabled: bool) -> tuple[bool, str]:
    """enabled=True 从 disabled 里移除，False 加进去。"""
    if not enabled and name in ESSENTIAL:
        return False, f"{name} 是 Hermes 的运行手册，禁不掉"
    cur = disabled()
    if enabled:
        if name not in cur:
            return True, "本来就是启用的"
        nxt = [n for n in cur if n != name]
        action = "enable"
    else:
        if name in cur:
            return True, "本来就是禁用的"
        nxt = cur + [name]
        action = "disable"
    ok, msg = _write(nxt)
    _audit(action, name, ok, msg)
    return ok, msg


def set_many(names: list[str], enabled: bool) -> dict:
    """批量开关：一次读配置、一次写、一次审计。

    单个失败（比如 hermes-agent）不影响其余，最后统一报哪些没成。
    """
    cur = disabled()
    cur_set = set(cur)
    skipped: list[str] = []
    target = list(cur)

    for name in names:
        if not enabled and name in ESSENTIAL:
            skipped.append(name)
            continue
        if enabled:
            if name in cur_set:
                cur_set.discard(name)
        else:
            if name not in cur_set:
                cur_set.add(name)

    target = sorted(cur_set)
    if target == sorted(cur):
        return {"ok": True, "changed": 0, "skipped": skipped, "message": "状态没有变化"}

    ok, msg = _write(target)
    _audit("batch-" + ("enable" if enabled else "disable"),
           f"{len(names)} 个", ok, f"跳过 {skipped}" if skipped else msg)
    return {"ok": ok, "changed": len(names) - len(skipped), "skipped": skipped,
            "message": msg, "disabled": target}


def toggle(name: str) -> tuple[bool, str]:
    return set_enabled(name, name in disabled())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("当前禁用：", disabled() or "（无）")
        raise SystemExit(0)
    action, _ = toggle(sys.argv[1])
    print(("✓ " if action else "✗ ") + sys.argv[1], "→ 禁用" if sys.argv[1] in disabled() else "→ 启用")
