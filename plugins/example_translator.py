"""示例：自定义翻译引擎。

拷一份改名就能用。这里用「全大写」当假翻译，方便肉眼确认链路通了 —— 真接一个
引擎就替换 translate() 里的实现。

挂上：config.toml 里写 engine = "plugins.example_translator:ShoutTranslator"
"""

from core.plugins import BaseTranslator


class ShoutTranslator(BaseTranslator):
    name = "shout"

    def __init__(self, proxy: str = "", **kwargs):
        # 框架会把 translate.proxy 传进来，用不上就收着
        self.proxy = proxy

    def translate(self, text: str) -> str:
        return text.upper()


# 如果文件里只有一个子类，配置里可以只写模块名：
#   engine = "plugins.example_translator"
