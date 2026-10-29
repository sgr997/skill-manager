# skill-manager

本地网页界面，用来浏览、审计、开关 AI agent 的 skill。

`~/.hermes/skills` 下堆到一百多个 `SKILL.md` 之后，装了什么、每轮吃多少 token、哪些压根没用过，就说不清了。这东西读那个目录，回答这几个问题。

没有构建步骤，没有数据库，前端不引框架。

## 能干什么

- **卡片网格**列全部 skill，按来源（hub / skills.sh / 本地 / 软链 / 核心）、状态、是否本地改过筛选
- **详情抽屉**带文件树，`SKILL.md` 渲染成 markdown，其它文件带行号显示，渲染和源码可切换
- **启用 / 禁用**写 Hermes 配置的 `skills.disabled`，禁掉就不再挂载
- **批量操作**一次选多个，一次写完
- **上下文开销**估算已启用的 skill 每轮往系统提示里塞多少 token
- **按来源更新**，hub 走自己的 content hash，skills.sh 走 `npx skills`
- **描述翻译**英文描述翻成中文，卡片和抽屉上都能切回原文

## 环境要求

- Python 3.11+（用了 `tomllib`）
- skill 目录结构是 `<分类>/<名字>/SKILL.md` 或 `<名字>/SKILL.md`
- 可选：装了 Hermes 且 `~/.hermes` 存在，开关和上游更新才能用

## 跑起来

```bash
git clone <this repo> skill-manager
cd skill-manager
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.toml config.toml   # 可以不做，默认值能跑
python3 server.py
```

打开 http://127.0.0.1:8900

没有鉴权，这是设计如此。它只听 `127.0.0.1`，要暴露到公网就自己在前面加带认证的反向代理，项目不关心你怎么保护它。

## 配置

`server.py` 旁边的 `config.toml`。每一项都有默认值，文件可以不建。完整清单见 `config.example.toml`。

```toml
[paths]
skill_roots = ["~/.hermes/skills"]     # 可以配多个，按 realpath 去重
hermes_config = "~/.hermes/config.yaml"

[translate]
proxy = ""                              # translate.googleapis.com 不通时填代理
```

环境变量能覆盖文件。`SKILLMGR_SERVER__PORT=9000` 改端口，单下划线分节，双下划线进二级键。

## 数据怎么算的

每个 skill 有四项指标，各有各的坑：

**体积**算的是 skill 目录下所有文件，不只是 `SKILL.md`。带 15MB 素材的 skill 就是真占 15MB。

**上下文 token** 估的是每轮成本。进系统提示的只有 `name: description` 那一行，不含正文。估算按中文 1 字 1 token、其余 3.5 字符 1 token。一律用 `len()/4` 会把中英混排的描述严重低估：在 111 个 skill 的真实库上，两种算法差 40%。

**调用次数**取两个来源的最大值：`.usage.json` 的 `use_count`（懒写入，有时晚几小时），和从 `state.db` 里解析出的 `skill_view` 调用（实时落库）。这统计的是**主动查阅**。被索引自动加载的 skill 不留痕迹，所以数字低不等于没用。

**来源**按顺序判定：Hermes hub lock、skills.sh lock、本地 `data/baseline.json`、最后算本地。软链挂载的 skill 同时给出链接路径和真实路径。

## 翻译

用 `translate.googleapis.com` 的免费端点，不要 key，不走大模型。两件事让它能用：

技术名词在发送前换成 `[[#N#]]` 占位符，翻完还原。不做这层，`Mermaid` 会变「美人鱼」、`HeyGen` 变「嘿根」。一开始试的是 Unicode 私用区字符，谷歌会把字符剥掉只留数字，所以改成 ASCII 记号。

译文缓存在 `data/desc_zh.json`，键是 skill 名字，同时存原文哈希。改了 `SKILL.md` 的 description，旧记录就对不上，界面退回显示英文原文，直到你重新翻。已删除的 skill 会在下次翻译时从缓存里清掉。

## 接口

除了三个 POST，其余全是只读。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/skills` | 列表 + 汇总，`?refresh=true` 绕过 30 秒缓存 |
| GET | `/api/skills/{name}` | 单个 skill 及文件树 |
| GET | `/api/skills/{name}/file?path=` | 文件内容或二进制元信息 |
| POST | `/api/skills/{name}/toggle` | `{"enabled": bool}` |
| POST | `/api/skills/batch-toggle` | `{"names": [...], "enabled": bool}` |
| POST | `/api/skills/{name}/update` | 从上游拉取 |
| POST | `/api/skills/translate` | `{"names": [...]}`，传 `{}` 翻下一批 |
| GET | `/api/me` | 版本信息 |
| GET | `/healthz` | 存活检查 |

## 目录结构

```
server.py           FastAPI 路由，30 秒响应缓存
core/config.py      配置读取，toml + 环境变量
core/scan.py        目录遍历、frontmatter 解析、token 估算
core/sources.py     上游 lock 文件、完整性比对
core/toggle.py      写 skills.disabled、审计日志
core/update.py      按来源分派更新
core/translate.py   谷歌端点、术语保护、缓存
static/             index.html、app.js、style.css、本地化的 marked.js
```

## 已知限制

- **frontmatter 解析依赖 `pyyaml`**。退化的逐行解析处理不了 `description: >` 折叠块，会静默报空描述。
- **`npx skills check` 不是试运行**，它直接执行更新。所以更新流程从不调 `check`，改为对标 `data/baseline.json`。
- **本地改过就不给更新**。内容哈希和上游对不上时更新按钮直接禁用，不偷偷覆盖你的改动。
- **禁用下一轮才生效**，不是立刻。系统提示每轮重建。
- **调用次数是下限**。自动加载不计数，见上面那条。

## 许可证

MIT
