"""配置读取：config.toml + 环境变量覆盖。

不写 config.toml 也能跑，默认值就是「本机单用户、只读 ~/.hermes/skills」的形态。
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path(os.environ.get("SKILLMGR_CONFIG") or (ROOT / "config.toml"))

DEFAULTS: dict = {
    "server": {
        "host": "127.0.0.1",
        "port": 8900,
    },
    "paths": {
        "skill_roots": ["~/.hermes/skills"],
        "usage_file": "~/.hermes/skills/.usage.json",
        "hermes_config": "~/.hermes/config.yaml",
        "hermes_home": "~/.hermes",
        "data_dir": "data",
    },
    "sources": {
        "hermes_hub_lock": "~/.hermes/skills/.hub/lock.json",
        "skills_sh_lock": "~/.agents/.skill-lock.json",
    },
    "translate": {
        "enabled": True,
        # 谷歌翻译的免费端点在部分网络环境需要代理；留空表示直连
        "proxy": "",
        "workers": 6,
        "endpoint": "https://translate.googleapis.com/translate_a/single",
    },
    "update": {
        # 关掉后页面不显示更新按钮，避免调用 hermes / npx
        "enabled": True,
        "hermes_cmd": "hermes",
        "npx_cmd": "npx",
        "timeout": 240,
    },
}

# 环境变量覆盖：KEY 用双下划线分段，例如 SKILLMGR_SERVER__PORT=9000
ENV_PREFIX = "SKILLMGR_"
ENV_ALIASES = {
    "SKILLMGR_DEV": ("server", "dev_mode"),
    "PORT": ("server", "port"),
}


def _merge(base: dict, over: dict) -> dict:
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _coerce(val: str):
    """环境变量一律是字符串，这里还原成原来的类型。

    列表必须按 JSON 解析 —— 直接把 '["/a"]' 当字符串塞进去，后面 iter 的时候
    会被逐字符拆成路径，其中 '/' 会变成扫描整个磁盘。
    """
    text = val.strip()
    if text[:1] in ("[", "{"):
        try:
            return json.loads(text)
        except Exception:
            return val
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    if text.lstrip("-").isdigit():
        return int(text)
    return val


def _expand(v):
    if isinstance(v, str) and v.startswith("~"):
        return os.path.expanduser(v)
    if isinstance(v, list):
        return [_expand(x) for x in v]
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    return v


def _load() -> dict:
    cfg = _merge({k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}, {})
    if CONFIG_FILE.is_file():
        try:
            with CONFIG_FILE.open("rb") as f:
                _merge(cfg, tomllib.load(f))
        except Exception as e:  # 配置写坏了要能看见原因，而不是静默用默认值
            print(f"[config] 解析 {CONFIG_FILE} 失败，改用默认值：{e}")

    for key, (section, field) in ENV_ALIASES.items():
        if os.environ.get(key):
            val = os.environ[key]
            if field == "port":
                val = int(val)
            cfg.setdefault(section, {})[field] = val

    for env_key, val in os.environ.items():
        if not env_key.startswith(ENV_PREFIX) or "__" not in env_key:
            continue
        parts = env_key[len(ENV_PREFIX):].lower().split("__")
        if len(parts) != 2:
            continue
        section, field = parts
        cfg.setdefault(section, {})[field] = _coerce(val)

    return _expand(cfg)


CFG = _load()


def path(*keys: str) -> Path:
    """取配置里的路径项，相对路径按项目根解析。"""
    cur = CFG
    for k in keys:
        cur = cur[k]
    p = Path(cur)
    return p if p.is_absolute() else (ROOT / p)


def data_dir() -> Path:
    d = path("paths", "data_dir")
    d.mkdir(parents=True, exist_ok=True)
    return d


def skill_roots() -> list[Path]:
    """配错的根目录要挡掉。

    扫 '/' 或 '$HOME' 这种会把整个磁盘走一遍，既慢又会撞权限错误。只收真实存在的
    目录，且至少要两级（/ 和 /Users 这种一律跳过）。
    """
    out: list[Path] = []
    for raw in CFG["paths"].get("skill_roots") or []:
        try:
            p = Path(os.path.expanduser(str(raw))).resolve()
        except Exception:
            continue
        if len(p.parts) < 3 or not p.is_dir():
            continue
        out.append(p)
    return out
