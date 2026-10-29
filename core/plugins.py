"""扩展点：翻译引擎。

插件是普通 Python 文件，放 `plugins/` 下，或者任何 import 得到的位置。配置里写
`模块路径:类名` 指过去，例如 `plugins.my_deepl:DeepLTranslator`。

基类只约定一个方法，术语保护、缓存、并发调度都由 core 里的框架负责，插件不用管。
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "plugins"


class BaseTranslator:
    """翻译引擎。

    输入一段纯文本，输出译文。不要在这里做术语保护或缓存 —— 框架已经做了，
    插件重复做会让占位符互相打架。
    """

    name = "base"

    def translate(self, text: str) -> str:
        raise NotImplementedError

    def translate_batch(self, texts: list[str]) -> list[str]:
        """默认逐条翻。引擎本身支持批量（DeepL、百度、腾讯云）就覆写这个。"""
        return [self.translate(t) for t in texts]


def load(spec: str):
    """按 `模块路径:类名` 或文件路径加载一个插件对象。

    支持三种写法：
        plugins.my_deepl:DeepLTranslator     import 得到
        plugins.my_deepl                     取模块里唯一一个基类子类
        /abs/path/foo.py:MyScanner           直接从文件加载，不用装包
    """
    if not spec or not isinstance(spec, str):
        raise ValueError(f"插件标识无效：{spec!r}")

    target, _, attr = spec.partition(":")
    target = target.strip()
    attr = attr.strip()

    mod = None
    if target.endswith(".py") or "/" in target:
        path = Path(target).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"插件文件不存在：{path}")
        mod_name = f"_skillmgr_plugin_{path.stem}"
        spec_obj = importlib.util.spec_from_file_location(mod_name, path)
        if spec_obj is None or spec_obj.loader is None:
            raise ImportError(f"无法加载插件文件：{path}")
        mod = importlib.util.module_from_spec(spec_obj)
        sys.modules[mod_name] = mod
        spec_obj.loader.exec_module(mod)
    else:
        # plugins.xxx 这种写法把项目根塞进 sys.path，省得用户装包
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        mod = importlib.import_module(target)

    if attr:
        obj = getattr(mod, attr, None)
        if obj is None:
            raise AttributeError(f"{target} 里没有 {attr}")
        return obj

    # 没写类名就自己找：模块里唯一一个实现了基类的类
    classes = [
        c for _, c in inspect.getmembers(mod, inspect.isclass)
        if issubclass(c, BaseTranslator) and c is not BaseTranslator
        and c.__module__ == mod.__name__
    ]
    if not classes:
        raise AttributeError(f"{target} 里没有任何 BaseTranslator 子类")
    if len(classes) > 1:
        names = ", ".join(c.__name__ for c in classes)
        raise AttributeError(f"{target} 里有多个候选（{names}），请在配置里写清 模块:类名")
    return classes[0]


def build(spec: str, factory, **kwargs):
    """加载并实例化，顺便校验它确实是想要的那种插件。"""
    obj = load(spec)
    cls = obj if inspect.isclass(obj) else obj.__class__
    if not issubclass(cls, factory):
        raise TypeError(f"{cls.__name__} 不是 {factory.__name__} 的子类")
    return cls(**kwargs) if inspect.isclass(obj) else obj


def discover() -> dict[str, list[str]]:
    """列出 plugins/ 下的文件，给文档和报错用。"""
    out: dict[str, list[str]] = {"files": []}
    if not PLUGIN_DIR.is_dir():
        return out
    for f in sorted(PLUGIN_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        out["files"] = out.get("files", []) + [f.name]
    return out
