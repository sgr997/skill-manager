#!/usr/bin/env python3
"""skill-manager 后端。

    python3 server.py        → http://127.0.0.1:8900

也可以只挂 /api/* 到自己的反代后面，静态页另发。

读写分离：只有 toggle / update / translate 会写磁盘，其余全是只读。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import config as cfg_mod
from core import scan as scan_mod
from core import sources, toggle, translate, update as update_mod

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
VERSION = "1.0.0"

# 预览上限：超过就只给前 N 字节，页面标明被截断
PREVIEW_LIMIT = 200 * 1024
TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh", ".js", ".ts",
            ".tsx", ".jsx", ".toml", ".cfg", ".ini", ".css", ".html", ".xml",
            ".csv", ".env", ".gitignore", ".lock"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".mov",
              ".mp3", ".wav", ".zip", ".tar", ".gz", ".pdf", ".woff", ".woff2",
              ".ttf", ".otf", ".ico", ".db", ".sqlite", ".pt", ".bin", ".onnx"}

app = FastAPI(title="skill manager")


# 30 秒缓存：/api/skills 要扫 111 个目录 + 给 21 个上游 skill 算 hash，
# 每次重跑 2 秒多，手机上点开就是一片空白等两秒。
_CACHE: dict = {"ts": 0.0, "payload": None}
CACHE_TTL = 30.0


def _cached(force: bool = False) -> dict:
    now = time.time()
    if not force and _CACHE["payload"] and now - _CACHE["ts"] < CACHE_TTL:
        return _CACHE["payload"]
    rows = scan_mod.scan()
    idx = sources.upstream_index()
    for r in rows:
        up = idx.get(r["name"])
        r["upstream"] = up
        r["integrity"] = "local"
        if up:
            try:
                r["integrity"] = sources.check(r["name"], Path(r["abs"]), up)["status"]
            except Exception:
                r["integrity"] = "unknown"
    # 有缓存译文就带上，没有就空串（前端据此决定显示原文还是中文）
    zh = translate.cached_map()
    for r in rows:
        rec = zh.get(r["name"]) or {}
        r["descZh"] = rec.get("zh", "") if rec.get("sig") == translate._sig(r["desc"]) else ""
    payload = {"rows": rows, "summary": scan_mod.summary(rows),
               "describe": translate.stats(rows),
               "platforms": sorted({p for r in rows for p in r["disabledPlatforms"]})}
    _CACHE.update({"ts": now, "payload": payload})
    return payload


def _find(name: str) -> dict:
    for r in scan_mod.scan():
        if r["name"] == name:
            return r
    raise HTTPException(status_code=404, detail=f"没有这个 skill：{name}")


def _safe_path(skill_dir: Path, rel: str) -> Path:
    """文件路径必须落在 skill 目录内，挡掉 ../ 穿越和软链外逃。"""
    target = (skill_dir / rel).resolve()
    root = skill_dir.resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="路径越界")
    return target


def _tree(skill_dir: Path, rel: str = "") -> list[dict]:
    root = skill_dir.resolve()
    out: list[dict] = []

    def walk(d: Path) -> None:
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return
        for p in entries:
            if p.name.startswith(".") and p.name not in (".env", ".gitignore"):
                continue
            r = p.relative_to(root).as_posix()
            if p.is_dir():
                out.append({"path": r, "dir": True, "size": 0})
                walk(p)
            else:
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                out.append({"path": r, "dir": False, "size": size,
                            "text": p.suffix.lower() in TEXT_EXT or not p.suffix})

    walk(root)
    return out


@app.get("/api/skills")
def api_skills(refresh: bool = False):
    return _cached(force=refresh)


@app.get("/api/skills/{name}")
def api_skill(name: str):
    r = _find(name)
    r["tree"] = _tree(Path(r["abs"]))
    return r


@app.get("/api/skills/{name}/file")
def api_file(name: str, path: str):
    r = _find(name)
    d = Path(r["abs"])
    target = _safe_path(d, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="没有这个文件")
    size = target.stat().st_size
    ext = target.suffix.lower()
    if ext in BINARY_EXT:
        return {"binary": True, "size": size, "ext": ext, "path": path}
    try:
        data = target.read_bytes()
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    truncated = len(data) > PREVIEW_LIMIT
    if truncated:
        data = data[:PREVIEW_LIMIT]
    text = data.decode("utf-8", errors="replace")
    return {"binary": False, "text": text, "size": size, "truncated": truncated,
            "lines": text.count("\n") + 1, "path": path}


@app.post("/api/skills/{name}/toggle")
async def api_toggle(name: str, request: Request):
    body = await request.json()
    enabled = bool(body.get("enabled"))
    ok, msg = toggle.set_enabled(name, enabled)
    _CACHE["payload"] = None
    return {"ok": ok, "message": msg, "enabled": enabled}


@app.post("/api/skills/translate")
async def api_translate(request: Request):
    """翻一批，默认取还没翻的前 N 条。前端循环调用直到 pending 归零。"""
    body = await request.json()
    payload = _cached()
    rows = payload["rows"]
    want = body.get("names")
    if want:
        picked = [r for r in rows if r["name"] in set(want)]
    else:
        zh = translate.cached_map()
        picked = [r for r in rows
                  if r["desc"] and not translate.has_chinese(r["desc"])
                  and (zh.get(r["name"], {}).get("sig") != translate._sig(r["desc"]))]
        # 谷歌是按条并发调的，一批 40 条控制在一次请求的超时内
        picked = picked[: int(body.get("limit") or 40)]

    items = [(r["name"], r["desc"]) for r in picked if r["desc"]]
    try:
        got = translate.translate(items, force=bool(body.get("force")))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"翻译失败：{e}")

    # 顺手清掉已删除 skill 的残留记录
    pruned = translate.prune({r["name"] for r in rows})
    _CACHE["payload"] = None  # 让下次列表带上新译文
    return {"translated": got, "count": len(got), "remaining": translate.pending(rows),
            "pruned": pruned}


@app.post("/api/skills/batch-toggle")
async def api_batch_toggle(request: Request):
    body = await request.json()
    names = [str(n) for n in (body.get("names") or [])][:300]
    if not names:
        raise HTTPException(status_code=400, detail="没有选中任何 skill")
    out = toggle.set_many(names, bool(body.get("enabled")))
    _CACHE["payload"] = None
    return out


@app.post("/api/skills/{name}/update")
async def api_update(name: str, request: Request):
    _CACHE["payload"] = None
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    r = _find(name)
    out = update_mod.update(name, Path(r["abs"]), force=bool(body.get("force")))
    _CACHE["payload"] = None
    return out


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/me")
def api_me():
    """当前用户。鉴权交给前面的反向代理，这里只报版本信息。"""
    return {"name": "local", "version": VERSION}


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S")}


# 独立跑和挂在 ~/www 下共用同一套资源路径
app.mount("/sm-static", StaticFiles(directory=str(STATIC)), name="sm-static")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


if __name__ == "__main__":
    import uvicorn

    srv = cfg_mod.CFG["server"]
    uvicorn.run(app, host=srv.get("host") or "127.0.0.1",
                port=int(os.environ.get("PORT") or srv.get("port") or 8900),
                log_level="warning")
