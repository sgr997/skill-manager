/* skill 管理面板前端 —— 原生 JS + marked，零构建 */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  rows: [], summary: null, q: "", filter: "all", sort: "size",
  current: null, tree: [], file: null, mode: "render",
  picking: false, selected: new Set(),
  descLang: "zh", translating: false,
  lang: {},          // 单个 skill 覆盖全局：name → "zh" | "en"
  trBusy: new Set(), // 正在翻的 skill，按钮显示 …
};

const SORTS = {
  size: (a, b) => b.size - a.size,
  use: (a, b) => b.use - a.use,
  name: (a, b) => a.name.localeCompare(b.name),
  mtime: (a, b) => b.mtime - a.mtime,
  files: (a, b) => b.files - a.files,
};

const FILTERS = {
  all: () => true,
  on: (r) => !r.disabled,
  off: (r) => r.disabled,
  // 来源
  hub: (r) => r.upstream?.kind === "hermes-hub",
  sh: (r) => r.upstream?.kind === "skills-sh",
  local: (r) => !r.upstream,
  link: (r) => r.link,
  core: (r) => r.name === "hermes-agent",
  // 状态
  dirty: (r) => r.integrity === "modified",
  big: (r) => r.size > 512 * 1024,
};

const num = (n) => (n || 0).toLocaleString("en-US");
const mb = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.round(n / 1024) + " KB");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// marked 只解析本地 skill 文件，输入不来自外部，因此不再接一层 DOMPurify。
marked.setOptions({ gfm: true, breaks: false });

async function api(path, opts) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 403) throw new Error("没有权限，请用管理员邮箱登录");
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.message || d.detail || `HTTP ${r.status}`);
  return d;
}

let toastTimer;
function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast on" + (kind ? " " + kind : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), 2800);
}

/* ── 卡片渲染 ─────────────────────────── */

function visible() {
  const q = state.q.trim().toLowerCase();
  const f = FILTERS[state.filter] || FILTERS.all;
  return state.rows
    .filter(f)
    .filter((r) => !q || r.name.toLowerCase().includes(q) || (r.desc || "").toLowerCase().includes(q))
    .sort(SORTS[state.sort]);
}

// 描述本来就是中文的不用给翻译按钮
function alreadyZh(text) {
  if (!text) return false;
  const cjk = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  return cjk / Math.max(text.length, 1) > 0.3;
}

function langOf(r) {
  return state.lang[r.name] || state.descLang;
}

function descText(r) {
  if (!r.desc) return "暂无描述";
  if (langOf(r) === "zh" && r.descZh) return r.descZh;
  return r.desc;
}

/* 按钮上写的是「点了会切到哪」：没译文就是「译」，有译文就在 EN / 中 之间切 */
function trLabel(r) {
  if (state.trBusy.has(r.name)) return "…";
  if (!r.descZh) return "译";
  return langOf(r) === "zh" ? "EN" : "中";
}

function tagsFor(r) {
  const out = [];
  if (r.name === "hermes-agent") {
    out.push('<span class="tag essent" title="系统提示无条件指向它，禁不掉">核心</span>');
    return out.join("");
  }
  if (r.upstream) {
    const hub = r.upstream.kind === "hermes-hub";
    out.push(`<span class="tag ${hub ? "hub" : "sh"}" title="${hub ? "来自 Hermes hub" : "来自 skills.sh"}，可更新">${hub ? "hub" : "skills.sh"}</span>`);
  }
  if (r.integrity === "modified") {
    out.push('<span class="tag dirty" title="内容和上游记录不一致，更新会覆盖，所以不给更新按钮">已改</span>');
  }
  if (r.link) out.push('<span class="tag link">软链</span>');
  return out.join("");
}

function cardHtml(r) {
  const usable = r.name !== "hermes-agent";
  const sel = state.selected.has(r.name);
  return `<article class="card${r.disabled ? " off" : ""}${sel ? " sel" : ""}" data-name="${esc(r.name)}">
    <span class="pick" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></span>
    <div class="card-top">
      <div class="card-name" title="${esc(r.name)}">${esc(r.name)}</div>
      ${tagsFor(r)}
      <button class="sw${r.disabled ? " off" : ""}" role="switch"
              aria-checked="${r.disabled ? "false" : "true"}"
              data-toggle="${esc(r.name)}"${usable ? "" : " disabled"}
              aria-label="${r.disabled ? "启用" : "禁用"} ${esc(r.name)}"
              title="${usable ? (r.disabled ? "已禁用，点一下启用" : "已启用，点一下禁用") : "核心 skill，不能禁用"}"></button>
    </div>
    <div class="card-desc${r.desc ? "" : " none"}" title="${esc(langOf(r) === "zh" && r.descZh ? r.desc : (r.desc || ""))}">${esc(descText(r))}</div>
    <div class="card-foot">
      <span><b>${mb(r.size)}</b></span><span class="dot">·</span>
      <span><b>${r.files}</b> 文件</span>
      ${r.refs ? `<span class="dot">·</span><span><b>${r.refs}</b> 处引用</span>` : ""}
      <span class="spacer">${r.disabled ? '<span class="card-off-flag">已禁用</span>' : `<span title="每轮注入系统提示的索引开销">≈${num(r.ctxTokens)} tok</span>`}</span>
      ${r.name !== "hermes-agent" && r.desc && !alreadyZh(r.desc) ? `<button class="trbtn${r.descZh ? " has" : ""}" data-tr="${esc(r.name)}"
        title="${r.descZh ? (langOf(r) === "zh" ? "切回英文原文" : "切回中文译文") : "把这句描述翻成中文"}"
        >${trLabel(r)}</button>` : ""}
    </div>
  </article>`;
}

function recomputeCtx() {
  if (!state.summary) return;
  const active = state.rows.filter((r) => !r.disabled);
  state.summary.ctxTokens = active.reduce((a, r) => a + (r.ctxTokens || 0), 0);
  state.summary.ctxChars = active.reduce((a, r) => a + (r.ctxChars || 0), 0);
  state.summary.savedTokens = state.rows
    .filter((r) => r.disabled)
    .reduce((a, r) => a + (r.ctxTokens || 0), 0);
  state.summary.disabled = state.rows.filter((r) => r.disabled).length;
}

function render() {
  const rows = visible();
  $("#grid").innerHTML = rows.length
    ? rows.map(cardHtml).join("")
    : '<div class="empty"><b>没有匹配的 skill</b>换个关键词或筛选条件试试</div>';
  $("#count").textContent = rows.length === state.rows.length
    ? `共 ${state.rows.length} 个`
    : `${rows.length} / ${state.rows.length}`;
}

function renderTrBar() {
  const d = state.summary && state.describe;
  const bar = $("#trbar");
  if (!bar) return;
  const left = state.describe ? state.describe.pending : 0;
  // 先更新文案再决定藏不藏，否则翻完最后一次仍留着「翻译中…」
  $("#trgo").textContent = state.translating ? "翻译中…" : "翻成中文";
  $("#trgo").disabled = state.translating;
  if (!left) { bar.hidden = true; return; }
  bar.hidden = false;
  $("#trtext").textContent = `还有 ${left} 条描述是英文`;
}

function renderHead() {
  const s = state.summary;
  if (!s) return;
  $("#stats").innerHTML = [
    `<span class="stat hi" title="已启用的 skill 每轮对话都会随系统提示注入，这部分是常驻开销"><b>≈${num(s.ctxTokens)}</b> tokens</span>`,
    `<span class="stat"><b>${s.total}</b> 个</span>`,
    `<span class="stat opt"><b>${mb(s.bytes)}</b> 磁盘</span>`,
    `<span class="stat opt"><b>${s.upstream}</b> 有上游</span>`,
    `<span class="stat${s.disabled ? " hi" : ""}"><b>${s.disabled}</b> 已禁用${s.savedTokens ? ` · 省 ${num(s.savedTokens)}` : ""}</span>`,
    `<span class="stat opt"><b>${s.linked}</b> 软链</span>`,
  ].join("");
  const g = $("#gen");
  if (g) g.textContent = `扫描于 ${s.generated}`;
}

async function load(refresh = false) {
  try {
    const d = await api("/api/skills" + (refresh ? "?refresh=true" : ""));
    state.rows = d.rows;
    state.summary = d.summary;
    state.describe = d.describe || null;
    renderHead();
    renderTrBar();
    render();
  } catch (e) {
    $("#grid").innerHTML = `<div class="empty"><b>加载失败</b>${esc(e.message)}</div>`;
  }
}

/* ── 翻译描述 ─────────────────────────── */

async function runTranslate() {
  if (state.translating) return;
  state.translating = true;
  renderTrBar();
  let done = 0, left = state.describe?.pending ?? 0, round = 0;
  try {
    while (round++ < 12) {
      const d = await api("/api/skills/translate", { method: "POST", body: JSON.stringify({ limit: 40 }) });
      done += d.count;
      left = d.remaining;
      if (!d.count) break;
      // 立刻把译文贴到卡片上，不用等全部跑完
      for (const [name, zh] of Object.entries(d.translated || {})) {
        const row = state.rows.find((r) => r.name === name);
        if (row) row.descZh = zh;
      }
      $("#trtext").textContent = `已翻 ${done} 条，还剩 ${left}`;
      render();
      if (!left) break;
    }
    state.describe.pending = left;
    state.lang = {};
    toast(left ? `翻了 ${done} 条，还剩 ${left} 条没成` : `全部翻完，共 ${done} 条`, left ? "bad" : "good");
  } catch (e) {
    toast("翻译失败：" + e.message, "bad");
  } finally {
    state.translating = false;
    renderTrBar();
  }
}

async function translateOne(name) {
  const row = state.rows.find((r) => r.name === name);
  if (!row || !row.desc) return;

  // 已经翻过：只在中文/原文之间切，不再打接口
  if (row.descZh) {
    state.lang[name] = langOf(row) === "zh" ? "en" : "zh";
    render();
    if (state.current?.name === name) paintPanelDesc();
    return;
  }

  state.trBusy.add(name);
  render();
  try {
    const d = await api("/api/skills/translate", {
      method: "POST", body: JSON.stringify({ names: [name] }),
    });
    const zh = (d.translated || {})[name];
    if (!zh) throw new Error("没拿到译文");
    row.descZh = zh;
    state.lang[name] = "zh";
    if (state.describe) state.describe.pending = Math.max(0, (state.describe.pending || 1) - 1);
    renderTrBar();
    toast(`已翻译 ${name}`, "good");
  } catch (e) {
    toast("翻译失败：" + e.message, "bad");
  } finally {
    state.trBusy.delete(name);
    render();
    if (state.current?.name === name) paintPanelDesc();
  }
}

/* ── 批量选择 ─────────────────────────── */

const BATCH_ALLOWED = (r) => r.name !== "hermes-agent";

function updateBatchBar() {
  $("#bcount").textContent = state.selected.size;
  const wasted = state.rows
    .filter((r) => state.selected.has(r.name) && !r.disabled)
    .reduce((a, r) => a + (r.ctxTokens || 0), 0);
  const el = $("#bsave");
  if (el) el.textContent = wasted ? `可省 ${num(wasted)}` : "";
  $("#batchbar").classList.toggle("on", state.picking);
  $("#btn-batch").setAttribute("aria-pressed", String(state.picking));
  document.body.classList.toggle("picking", state.picking);
}

function setPicking(on) {
  state.picking = on;
  if (!on) state.selected.clear();
  updateBatchBar();
  render();
}

function togglePick(name, force) {
  const has = state.selected.has(name);
  const want = force === undefined ? !has : force;
  if (want) state.selected.add(name); else state.selected.delete(name);
  const card = $(`.card[data-name="${CSS.escape(name)}"]`);
  if (card) card.classList.toggle("sel", want);
  updateBatchBar();
}

async function batchToggle(enabled) {
  const names = [...state.selected].filter((n) => n !== "hermes-agent");
  if (!names.length) { toast("先选几个再操作", "bad"); return; }
  if (names.length > 5) {
    const verb = enabled ? "启用" : "禁用";
    if (!confirm(`确定${verb}这 ${names.length} 个 skill？${enabled ? "" : "下一轮对话生效。"}`)) return;
  }
  try {
    const d = await api("/api/skills/batch-toggle", {
      method: "POST", body: JSON.stringify({ names, enabled }),
    });
    if (!d.ok) throw new Error(d.message || "批量操作失败");
    const set = new Set(names);
    state.rows.forEach((r) => { if (set.has(r.name)) r.disabled = !enabled; });
    if (state.summary) {
      recomputeCtx();
      const t = new Date();
      const p = (n) => String(n).padStart(2, "0");
      state.summary.generated = `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}`;
    }
    renderHead();
    const skipped = (d.skipped || []).length;
    toast(`${enabled ? "已启用" : "已禁用"} ${d.changed} 个${skipped ? `，${skipped} 个跳过` : ""}`,
          skipped ? "bad" : "good");
    state.selected.clear();
    updateBatchBar();
    render();
  } catch (e) {
    toast(e.message, "bad");
  }
}

/* ── 抽屉 ─────────────────────────────── */

function setMode(mode) {
  state.mode = mode;
  $$("#p-mode button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.m === mode)));
  paintFile();
}

function paintPanelDesc() {
  const row = state.current;
  if (!row) return;
  const el = $("#p-desc");
  const btn = $("#p-tr");
  el.textContent = descText(row);
  el.classList.toggle("none", !row.desc);
  // 没描述、或本来就是中文，不给按钮
  if (!row.desc || alreadyZh(row.desc)) {
    btn.hidden = true;
    return;
  }
  btn.hidden = false;
  btn.textContent = trLabel(row);
  btn.classList.toggle("has", !!row.descZh);
  btn.title = row.descZh
    ? (langOf(row) === "zh" ? "切回英文原文" : "切回中文译文")
    : "把这句描述翻成中文";
}

async function openPanel(name) {
  const row = state.rows.find((r) => r.name === name);
  if (!row) return;
  state.current = row;
  state.file = null;
  state.tree = [];

  $("#p-name").textContent = row.name;
  paintPanelDesc();
  $("#p-facts").innerHTML = [
    `<span>路径 <b>${esc(row.path || row.name)}</b></span>`,

    `<span>体积 <b>${mb(row.size)}</b></span>`,
    `<span><b>${row.files}</b> 文件</span>`,
    row.refs ? `<span><b>${row.refs}</b> refs</span>` : "",
    `<span>索引 <b>≈${num(row.ctxTokens)}</b> tokens</span>`,
    `<span>调用 <b>${row.use}</b> 次</span>`,
    row.patches ? `<span>改过 <b>${row.patches}</b> 次</span>` : "",
  ].join("");

  const up = row.upstream;
  if (up) {
    const where = up.url
      ? `<a href="${esc(up.url)}" target="_blank" rel="noreferrer">${esc(up.url.replace(/^https?:\/\//, ""))}</a>`
      : esc(up.identifier || up.upstream);
    $("#p-src").innerHTML = `<span class="k">来源</span>${up.kind === "hermes-hub" ? "Hermes hub" : "skills.sh"} · ${where}`;
  } else {
    $("#p-src").innerHTML = `<span class="k">来源</span>本地自己写的，没有上游`;
  }
  if (row.link && row.realPath) {
    $("#p-src").innerHTML += `<br><span class="k">软链</span>${esc(row.abs)}<br>` +
      `<span class="k">指向</span><code>${esc(row.realPath)}</code>`;
  }

  const upd = $("#p-update");
  if (!up) { upd.disabled = true; upd.textContent = "本地 skill"; }
  else if (row.integrity === "modified") { upd.disabled = true; upd.textContent = "本地已改"; }
  else { upd.disabled = false; upd.textContent = "更新"; }

  const hint = $("#p-hint");
  if (row.integrity === "modified") {
    hint.style.display = "";
    hint.textContent = "本地内容与上游记录不一致，更新会覆盖这些改动，所以这里不给按钮。真要覆盖就用命令行加 --force。";
  } else hint.style.display = "none";

  const tgl = $("#p-toggle");
  tgl.textContent = row.disabled ? "启用" : "禁用";
  tgl.className = "btn" + (row.disabled ? " primary" : "");

  $("#p-tree").innerHTML = '<div class="tree-item dir">加载中</div>';
  $("#p-view").innerHTML = "";
  $("#p-fname").textContent = "";
  $("#p-mode").style.visibility = "hidden";
  $("#panel").classList.add("on");
  $("#scrim").classList.add("on");

  try {
    const d = await api(`/api/skills/${encodeURIComponent(name)}`);
    state.tree = d.tree || [];
    renderTree(state.tree);
    const first = state.tree.find((t) => t.path === "SKILL.md") || state.tree.find((t) => !t.dir);
    if (first) openFile(first.path);
  } catch (e) {
    $("#p-tree").innerHTML = `<div class="tree-item">${esc(e.message)}</div>`;
  }
}

function renderTree(tree) {
  const box = $("#p-tree");
  if (!tree.length) { box.innerHTML = '<div class="tree-item dir">空目录</div>'; return; }
  box.innerHTML = tree.map((t) => {
    if (t.dir) return `<div class="tree-item dir">${esc(t.path)}</div>`;
    const depth = t.path.split("/").length - 1;
    return `<div class="tree-item" data-file="${esc(t.path)}" style="padding-left:${16 + depth * 11}px">
      <span class="fn">${esc(t.path.split("/").pop())}</span>
      <span class="sz">${t.size >= 1024 ? Math.round(t.size / 1024) + "K" : t.size}</span>
    </div>`;
  }).join("");
}

async function openFile(path) {
  const name = state.current?.name;
  if (!name) return;
  $$(".tree-item[data-file]").forEach((el) => el.removeAttribute("data-active"));
  const active = $(`.tree-item[data-file="${CSS.escape(path)}"]`);
  if (active) active.dataset.active = "1";

  $("#p-view").innerHTML = '<div class="binary">读取中</div>';
  try {
    const d = await api(`/api/skills/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`);
    d.path = path;
    state.file = d;
    const isMd = /\.(md|markdown)$/i.test(path);
    $("#p-fname").textContent = path + (d.truncated ? "  （已截断）" : "");
    $("#p-mode").style.visibility = d.binary ? "hidden" : "visible";
    $("#p-mode button[data-m=render]").disabled = !isMd;
    // markdown 默认渲染；其它文件只能看源码
    state.mode = isMd ? (state.mode === "source" ? "source" : "render") : "source";
    $$("#p-mode button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.m === state.mode)));
    paintFile();
  } catch (e) {
    $("#p-view").innerHTML = `<div class="binary">${esc(e.message)}</div>`;
  }
}

function paintFile() {
  const d = state.file;
  if (!d) return;
  const view = $("#p-view");

  if (d.binary) {
    view.innerHTML = `<div class="binary"><span class="big">${esc((d.ext || "").replace(".", ""))}</span>
      二进制文件 · ${mb(d.size)}<br>不做渲染，需要看就看本地文件</div>`;
    return;
  }

  const isMd = /\.(md|markdown)$/i.test(d.path || "");
  if (state.mode === "render" && isMd) {
    view.innerHTML = `<div class="md">${marked.parse(d.text)}</div>`;
  } else {
    const lines = d.text.split("\n");
    // 文件末尾换行会多出一行空行，去掉后行号才和编辑器对齐。
    // 行号和正文必须来自同一个数组，否则两栏会错开一行。
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    const nums = lines.map((_, i) => i + 1).join("\n");
    view.innerHTML = `<div class="code-wrap">
        <pre class="code-ln">${nums}</pre>
        <pre class="code-body">${esc(lines.join("\n"))}</pre>
      </div>`;
  }
  view.scrollTop = 0;
}

function closePanel() {
  $("#panel").classList.remove("on");
  $("#scrim").classList.remove("on");
  state.current = null;
  state.file = null;
}

/* ── 动作 ─────────────────────────────── */

async function doToggle(name, enabled) {
  try {
    const d = await api(`/api/skills/${encodeURIComponent(name)}/toggle`, {
      method: "POST", body: JSON.stringify({ enabled }),
    });
    if (!d.ok) throw new Error(d.message);
    const row = state.rows.find((r) => r.name === name);
    if (row) row.disabled = !enabled;
    if (state.summary) {
      recomputeCtx();
      const t = new Date();
      const p = (n) => String(n).padStart(2, "0");
      state.summary.generated = `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}`;
    }
    renderHead();
    render();
    if (state.current?.name === name) {
      state.current.disabled = !enabled;
      const tgl = $("#p-toggle");
      tgl.textContent = enabled ? "禁用" : "启用";
      tgl.className = "btn" + (enabled ? "" : " primary");
    }
    toast(enabled ? `已启用 ${name}` : `已禁用 ${name}，下一轮对话生效`, "good");
  } catch (e) {
    toast(e.message, "bad");
  }
}

async function doUpdate() {
  const name = state.current?.name;
  if (!name) return;
  const btn = $("#p-update");
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "更新中…";
  try {
    const d = await api(`/api/skills/${encodeURIComponent(name)}/update`, { method: "POST", body: "{}" });
    toast(d.ok ? `${name} 已更新` : (d.message || "更新失败"), d.ok ? "good" : "bad");
    await load(true);
    await openPanel(name);
  } catch (e) {
    toast(e.message, "bad");
    btn.textContent = old;
    btn.disabled = false;
  }
}

/* ── 事件 ─────────────────────────────── */

$("#q").addEventListener("input", (e) => { state.q = e.target.value; render(); });

$("#seg").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-f]");
  if (!b) return;
  state.filter = b.dataset.f;
  $$("#seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  render();
});

$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; render(); });

// 筛选条横向滚动：到底了就把右侧渐隐去掉，否则会一直挡着最后一个按钮
function syncSegFade() {
  const seg = $("#seg");
  const atEnd = seg.scrollLeft + seg.clientWidth >= seg.scrollWidth - 2;
  seg.classList.toggle("fade", seg.scrollWidth > seg.clientWidth && !atEnd);
  seg.classList.toggle("fade-l", seg.scrollLeft > 2);
}

$("#seg").addEventListener("scroll", syncSegFade, { passive: true });
window.addEventListener("resize", syncSegFade);

$("#grid").addEventListener("click", (e) => {
  const tr = e.target.closest(".trbtn");
  if (tr && !tr.disabled) {
    e.stopPropagation();
    translateOne(tr.dataset.tr);
    return;
  }
  const sw = e.target.closest(".sw");
  if (sw && !sw.disabled) {
    const name = sw.dataset.toggle;
    const row = state.rows.find((r) => r.name === name);
    if (row) doToggle(name, row.disabled);
    return;
  }
  const card = e.target.closest(".card");
  if (!card) return;
  if (state.picking) {
    if (BATCH_ALLOWED({ name: card.dataset.name })) togglePick(card.dataset.name);
    else toast("核心 skill 不能禁用", "bad");
    return;
  }
  openPanel(card.dataset.name);
});

$("#p-tree").addEventListener("click", (e) => {
  const it = e.target.closest(".tree-item[data-file]");
  if (it) openFile(it.dataset.file);
});

$("#p-mode").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-m]");
  if (b && !b.disabled) setMode(b.dataset.m);
});

$("#trgo").addEventListener("click", runTranslate);
$("#trlang").addEventListener("click", () => {
  state.descLang = state.descLang === "zh" ? "en" : "zh";
  state.lang = {}; // 全局切换时把单条覆盖清掉，否则有的卡不跟着变
  $("#trlang").textContent = state.descLang === "zh" ? "原文" : "中文";
  render();
});

$("#btn-batch").addEventListener("click", () => setPicking(!state.picking));
$("#b-done").addEventListener("click", () => setPicking(false));
$("#b-none").addEventListener("click", () => { state.selected.clear(); updateBatchBar(); render(); });
$("#b-all").addEventListener("click", () => {
  visible().filter(BATCH_ALLOWED).forEach((r) => state.selected.add(r.name));
  updateBatchBar();
  render();
});
$("#b-on").addEventListener("click", () => batchToggle(true));
$("#b-off").addEventListener("click", () => batchToggle(false));

$("#p-tr").addEventListener("click", () => {
  if (state.current) translateOne(state.current.name);
});

$("#p-close").addEventListener("click", closePanel);
$("#scrim").addEventListener("click", closePanel);
$("#p-update").addEventListener("click", doUpdate);
$("#p-toggle").addEventListener("click", () => {
  if (state.current) doToggle(state.current.name, state.current.disabled);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (state.picking) { setPicking(false); return; }
    closePanel();
    return;
  }
  // 抽屉开着时 j / k 上下切文件
  if (!state.current || e.target.matches("input, textarea, select")) return;
  if (e.key !== "j" && e.key !== "k") return;
  const files = state.tree.filter((t) => !t.dir);
  const idx = files.findIndex((f) => f.path === state.file?.path);
  const next = files[idx + (e.key === "j" ? 1 : -1)];
  if (next) openFile(next.path);
});

/* 启动 */
(async () => {
  try {
    const me = await fetch("/api/me").then((r) => r.json());
    if (me.email) $("#me").textContent = me.email;
  } catch {}
  load();
})();
