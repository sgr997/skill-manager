"""放扩展插件的地方。

每个 .py 文件实现 core.plugins 里的一个基类，然后在 config.toml 里挂上：

    [translate]
    engine = "plugins.my_deepl:DeepLTranslator"

    [paths]
    scanners = ["plugins.my_source:RemoteScanner"]

文件名以 _ 开头的不参与自动发现，但显式写在配置里照样能加载。
"""
