"""skill 描述翻译 —— 走谷歌翻译的免费端点，不用大模型。

端点：translate.googleapis.com/translate_a/single（client=gtx，无 key，非官方）
国内直连不通，必须走本机代理；代理地址用 SKILLMGR_PROXY 覆盖。

缓存键是 skill 名字，值里存原文签名，描述改过自动重翻。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as cfg_mod
from . import plugins

GOOGLE_URL = cfg_mod.CFG["translate"].get("endpoint") or "https://translate.googleapis.com/translate_a/single"
PROXY = os.environ.get("SKILLMGR_PROXY", cfg_mod.CFG["translate"].get("proxy") or "")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
WORKERS = int(os.environ.get("SKILLMGR_TR_WORKERS", cfg_mod.CFG["translate"].get("workers") or 6))

CACHE_FILE = cfg_mod.data_dir() / "desc_zh.json"


def _load() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CACHE_FILE)


def _sig(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def has_chinese(text: str) -> bool:
    if not text:
        return False
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    # 中文占比超过三成就算已经中文了，不用翻
    return cjk / max(len(text), 1) > 0.3


def get_cached(name: str, desc: str) -> str:
    """缓存命中的译文；描述改过就返回空串。"""
    rec = _load().get(name) or {}
    if rec.get("sig") == _sig(desc):
        return rec.get("zh") or ""
    return ""


def cached_map() -> dict:
    """给列表页用：{name: {zh, sig}}"""
    return _load()


# 技术名词先换成私有区占位符，翻完还原。否则 Mermaid 会被音译成「美人鱼」、
# GitHub 变「GitHub仓库」、HeyGen 变「嘿根」这种。
PROTECT = [
    "Mermaid", "HeyGen", "HyperFrames", "GitHub", "GitLab", "Cloudflare", "Notion",
    "SwiftUI", "macOS", "iOS", "iPadOS", "Android", "Windows", "Linux", "Ubuntu",
    "JavaScript", "TypeScript", "Python", "Swift", "Rust", "Golang", "Node.js",
    "React", "Vue", "Next.js", "Tailwind", "Docker", "Kubernetes", "SQLite",
    "PostgreSQL", "Redis", "MySQL", "WebDAV", "HTTP", "HTTPS", "WebSocket",
    "HTML", "CSS", "SVG", "WebM", "MP4", "PNG", "JPEG", "PDF", "LaTeX",
    "API", "SDK", "CLI", "JSON", "YAML", "TOML", "URL", "URI", "DNS", "CDN",
    "VPN", "SSH", "TLS", "SSL", "JWT", "OAuth", "REST", "GraphQL", "gRPC",
    "Vercel", "Remotion", "Figma", "Slack", "Telegram", "Discord", "Spotify",
    "HuggingFace", "PyTorch", "TensorFlow", "OpenAI", "Claude", "Gemini",
    "DeepSeek", "Whisper", "Stable Diffusion", "ComfyUI", "FFmpeg", "OpenCV",
    "Home Assistant", "Philips Hue", "Xcode", "TestFlight", "App Store",
    "Reddit", "YouTube", "Twitter", "Bilibili", "TikTok", "Douyin",
]
_PROTECT_SORTED = sorted(set(PROTECT), key=len, reverse=True)


def _protect(text: str) -> tuple[str, dict]:
    """把技术名词换成 [[#N#]] 占位。

    试过私用区字符（\ue000N\ue001），谷歌会把它们剥掉只留数字。[[#N#]] 这类 ASCII
    记号实测原样保留，翻完按表还原。
    """
    marks = {}
    out = text
    for i, term in enumerate(_PROTECT_SORTED):
        if term in out:
            tok = f"[[#{i}#]]"
            marks[tok] = term
            out = out.replace(term, tok)
    return out, marks


def _restore(text: str, marks: dict) -> str:
    for tok, term in marks.items():
        text = text.replace(tok, term)
    return text


def _opener() -> urllib.request.OpenerDirector:
    handler = (urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
               if PROXY else urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(handler)


class GoogleTranslator(plugins.BaseTranslator):
    """谷歌免费端点。不要 key，国内直连不通时要配代理。"""

    name = "google"

    def __init__(self, endpoint: str | None = None, proxy: str | None = None,
                 attempts: int = 3, timeout: int = 20):
        self.endpoint = endpoint or GOOGLE_URL
        self.proxy = PROXY if proxy is None else proxy
        self.attempts = attempts
        self.timeout = timeout
        self._opener = None

    def _http(self) -> urllib.request.OpenerDirector:
        if self._opener is None:
            handler = (urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
                       if self.proxy else urllib.request.ProxyHandler({}))
            self._opener = urllib.request.build_opener(handler)
        return self._opener

    def translate(self, text: str) -> str:
        """翻一条。谷歌返回嵌套数组，把各段拼起来。"""
        q = urllib.parse.urlencode(
            {"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text}
        )
        req = urllib.request.Request(f"{self.endpoint}?{q}", headers={"User-Agent": UA})
        opener = self._http()

        for i in range(self.attempts):
            try:
                with opener.open(req, timeout=self.timeout) as r:
                    data = json.loads(r.read())
                parts = [seg[0] for seg in (data[0] or []) if seg and seg[0]]
                return "".join(parts)
            except Exception as e:
                if i == self.attempts - 1:
                    raise RuntimeError(f"翻译失败：{e}") from e
                time.sleep(2 * (i + 1))
        return ""


class NullTranslator(plugins.BaseTranslator):
    """不翻译，原样返回。关掉翻译功能时用它。"""

    name = "none"

    def translate(self, text: str) -> str:
        return text


_BUILTIN = {"google": GoogleTranslator, "none": NullTranslator}
_engine = None


def engine() -> plugins.BaseTranslator:
    """当前引擎，按配置实例化一次后复用。"""
    global _engine
    if _engine is not None:
        return _engine

    spec = (cfg_mod.CFG["translate"].get("engine") or "google").strip()
    tcfg = cfg_mod.CFG["translate"]
    if spec in _BUILTIN:
        _engine = _BUILTIN[spec](proxy=tcfg.get("proxy") or "",
                                 endpoint=tcfg.get("endpoint")) if spec == "google" \
            else _BUILTIN[spec]()
    else:
        # 插件引擎把代理地址传进去（它不一定要用，收着不碍事）
        try:
            _engine = plugins.build(spec, plugins.BaseTranslator)
        except TypeError:
            _engine = plugins.build(spec, plugins.BaseTranslator, proxy=tcfg.get("proxy") or "")
    return _engine


def reset_engine() -> None:
    """改完配置调一下，下次取引擎时重新实例化。"""
    global _engine
    _engine = None


def translate(names_descs: list[tuple[str, str]], force: bool = False) -> dict:
    """并发翻一批，返回 {name: zh}。跳过已有中文和空描述的。"""
    cache = _load()
    out, todo = {}, []

    for name, desc in names_descs:
        if not desc or not desc.strip():
            continue
        if has_chinese(desc):
            continue
        rec = cache.get(name) or {}
        if not force and rec.get("sig") == _sig(desc):
            out[name] = rec["zh"]
            continue
        todo.append((name, desc))

    if not todo:
        return out

    tr = engine()

    def work(item):
        name, desc = item
        # 术语保护放在框架层：换任何引擎都自动受保护，插件不用重复实现
        safe, marks = _protect(desc)
        try:
            out = tr.translate(safe)
        except Exception:
            return name, desc, ""
        # 折叠块原文带硬换行，卡片上会断得很难看，压成单行
        return name, desc, re.sub(r"\s+", " ", _restore(out or "", marks)).strip()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(work, todo))

    now = time.strftime("%Y-%m-%d %H:%M")
    for name, desc, zh in results:
        if not zh:
            continue
        cache[name] = {"sig": _sig(desc), "zh": zh, "at": now}
        out[name] = zh

    _save(cache)
    return out


def prune(alive: set[str]) -> int:
    """删掉已经不存在的 skill 的缓存记录，返回删了几条。

    skill 被删除或改名后，它的条目会一直躺在 desc_zh.json 里。翻译时顺手清掉，
    免得越攒越多。
    """
    cache = _load()
    dead = [k for k in cache if k not in alive]
    if not dead:
        return 0
    for k in dead:
        del cache[k]
    _save(cache)
    return len(dead)


def pending(rows: list[dict]) -> int:
    """还有多少条待翻（给前端显示进度用）。"""
    cache = _load()
    n = 0
    for r in rows:
        desc = r.get("desc") or ""
        if not desc.strip() or has_chinese(desc):
            continue
        rec = cache.get(r["name"]) or {}
        if rec.get("sig") != _sig(desc):
            n += 1
    return n


def stats(rows: list[dict]) -> dict:
    cache = _load()
    done = 0
    for r in rows:
        desc = r.get("desc") or ""
        rec = cache.get(r["name"]) or {}
        if desc and rec.get("sig") == _sig(desc):
            done += 1
    return {"done": done, "pending": pending(rows), "total": len(rows), "backend": "google"}
