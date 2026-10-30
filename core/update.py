"""更新上游 skill。

两条来源两条路：
  hermes-hub  → hermes skills update <name>    自带 content_hash 比对，本地改过的默认跳过
  skills-sh   → npx skills update <name>      没有 dry-run、没有保护，覆盖前必须自己挡

skills.sh 那条尤其要小心：`npx skills check` 会直接执行更新，不是只看一眼。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import sources

PROXY = {"https_proxy": "http://127.0.0.1:7890", "http_proxy": "http://127.0.0.1:7890"}
from . import config as cfg_mod

AUDIT = cfg_mod.data_dir() / "audit.log"


def _audit(action: str, name: str, ok: bool, detail: str = "") -> None:
    import time
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    try:
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{action}\t{name}\t"
                    f"{'ok' if ok else 'FAIL'}\t{detail}\n")
    except OSError:
        pass


def _run(cmd: list[str], timeout: int = 240) -> tuple[bool, str]:
    env = dict(os.environ)
    env.update(PROXY)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd="/tmp")
    except subprocess.TimeoutExpired:
        return False, f"超时（{timeout}s）"
    except OSError as e:
        return False, f"调用失败：{e}"
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode == 0, out[-1200:]


def update(name: str, path: Path, force: bool = False) -> dict:
    """返回 {ok, message, status}"""
    up = sources.upstream_index().get(name)
    if not up:
        return {"ok": False, "message": "这个 skill 没有上游来源，无法更新", "status": "local"}

    check = sources.check(name, path, up)
    if check["status"] == "modified" and not force:
        _audit("update-skip", name, False, "本地有修改")
        return {"ok": False, "status": "modified",
                "message": "本地有修改，跳过。要覆盖请走命令行加 --force"}

    if up["kind"] == "hermes-hub":
        ok, out = _run([cfg_mod.resolve_cmd(cfg_mod.CFG["update"].get("hermes_cmd") or "hermes"), "skills", "update", name] + (["--force"] if force else []))
    else:
        ok, out = _run([cfg_mod.resolve_cmd(cfg_mod.CFG["update"].get("npx_cmd") or "npx"),
              "-y", "skills@latest", "update", name], timeout=300)

    if ok:
        sources.rebaseline(name, path)
        # 再算一次，确认内容真的动了（上游可能已经是最新，命令成功但无变化）
        after = sources.check(name, path, sources.upstream_index().get(name))
        if after["status"] == "modified":
            ok, out = False, "更新命令返回成功，但内容和记录仍不一致，可能上游已改结构"

    _audit("update", name, ok, out[:300].replace("\n", " "))
    return {"ok": ok, "status": check["status"], "message": out[-600:] or ("已更新" if ok else "失败")}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 -m core.update <name> [--force]")
        raise SystemExit(2)
    nm = sys.argv[1]
    idx = sources.upstream_index()
    if nm not in idx:
        print(f"{nm} 没有上游来源")
        raise SystemExit(1)
    p = Path.home() / ".hermes" / "skills" / idx[nm].get("installPath", nm)
    print(sources.check(nm, p, idx[nm]))
