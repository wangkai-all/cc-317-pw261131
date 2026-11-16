/* 成本分摊演示前端：原生 JS，无第三方依赖。 */
"use strict";

const state = {
  evidence: [],          // 证据版摘要
  evidenceDetail: {},    // version -> 完整证据
  reports: [],           // 报告列表
  currentVersion: null,
  currentScheme: "OCCUPANCY",
  currentReport: null,
  lineFilter: "ALL",
};

const CATEGORY_LABEL = {
  COMMITTED: "承诺成本",
  RETRY_WASTE: "重试额外用量",
  SPEC_WASTE: "投机额外用量",
  RECOMPUTE_WASTE: "并行重算用量",
};
const PRODUCT_NAME = {}; // 加载后填充

const yuan = (cents) => "¥" + (cents / 100).toLocaleString("zh-CN",
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const shortHash = (h) => h ? h.slice(0, 12) : "—";
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}

// ------------------------------------------------------------- 初始化
async function init() {
  bindStaticEvents();
  await refreshAll();
}

async function refreshAll() {
  const [evResp, repResp] = await Promise.all([
    api("/api/evidence"), api("/api/reports"),
  ]);
  state.evidence = evResp.items;
  state.reports = repResp.items;
  state.evidence.forEach((ev) => {
    // 产品名映射从证据详情按需加载，先用摘要里没有，占位
  });
  if (!state.currentVersion && state.evidence.length) {
    state.currentVersion = state.evidence[state.evidence.length - 1].version;
  }
  populateSelectors();
  await loadVersion();
  renderRevisions();
}

function populateSelectors() {
  const evSel = $("#evSelect");
  evSel.innerHTML = "";
  state.evidence.forEach((ev) => {
    const o = el("option", null, `${ev.version} · ${esc(ev.description)}`);
    o.value = ev.version;
    evSel.appendChild(o);
  });
  evSel.value = state.currentVersion;
}

async function loadEvidenceDetail(version) {
  if (!state.evidenceDetail[version]) {
    state.evidenceDetail[version] = await api("/api/evidence/detail?version=" + version);
  }
  return state.evidenceDetail[version];
}

async function loadVersion() {
  const version = state.currentVersion;
  const detail = await loadEvidenceDetail(version);
  detail.payload.products.forEach((p) => { PRODUCT_NAME[p.id] = p.name; });
  PRODUCT_NAME.PUBLIC_POOL = "平台公共成本池";

  const rid = `RPT-${version}-${state.currentScheme}`;
  state.currentReport = await api("/api/report?id=" + rid);
  renderOverview();
  renderDag(detail.payload, state.currentReport.body);
  await renderCompare();
  renderConfirmRow();
}

// ------------------------------------------------------------- 总览
function renderOverview() {
  const ev = state.evidence.find((e) => e.version === state.currentVersion);
  const body = state.currentReport.body;
  const c = body.conservation;

  $("#evMeta").innerHTML = "";
  $("#evMeta").append(
    el("span", "hash-chip", `版本 <b>${esc(ev.version)}</b>`),
    el("span", "hash-chip", `证据哈希 ${shortHash(ev.evidence_hash)}`),
    el("span", "hash-chip", `前序 ${shortHash(ev.previous_evidence_hash)}`),
    el("span", "hash-chip", `生成于 ${esc(ev.generated_at)}`),
    el("span", "hash-chip", `${ev.attempt_count} 次尝试 · ${ev.bill_count} 张账单 · ${ev.edge_count} 条需求边`),
  );

  const waste = body.products.reduce((s, p) =>
    s + p.by_category.RETRY_WASTE + p.by_category.SPEC_WASTE
    + p.by_category.RECOMPUTE_WASTE, 0)
    + body.public_pool.by_category.RETRY_WASTE;
  $("#kpiRow").innerHTML = "";
  [
    ["成本总额", yuan(c.total_cost_cents), ""],
    ["真实账单", yuan(c.billed_cents), ""],
    ["预提（账单迟到）", yuan(c.accrued_cents), c.accrued_cents ? "warn" : ""],
    ["额外用量合计", yuan(waste), "warn"],
    ["公共成本池", yuan(c.public_pool_cents), c.public_pool_cents ? "pool" : ""],
  ].forEach(([label, value, cls]) => {
    const k = el("div", "kpi " + cls);
    k.appendChild(el("div", "label", label));
    k.appendChild(el("div", "value", value));
    $("#kpiRow").appendChild(k);
  });

  // 产品表
  const cats = Object.keys(CATEGORY_LABEL);
  const head = $("#productTable thead tr");
  head.innerHTML = "<th>归集对象</th>" + cats.map((c2) =>
    `<th>${CATEGORY_LABEL[c2]}</th>`).join("") +
    '<th class="num">合计</th><th class="num">占比</th>';
  const tb = $("#productTable tbody");
  tb.innerHTML = "";
  const total = c.total_cost_cents || 1;
  body.products.forEach((p) => {
    const tr = el("tr");
    tr.innerHTML = `<td><b>${esc(p.name)}</b><br><span class="muted">${esc(p.product_id)} · ${esc(p.owner)}</span></td>`
      + cats.map((k2) => `<td class="num">${yuan(p.by_category[k2])}</td>`).join("")
      + `<td class="num"><b>${yuan(p.total_cents)}</b></td>`
      + `<td class="num">${(p.total_cents / total * 100).toFixed(1)}%</td>`;
    tb.appendChild(tr);
  });
  const pool = body.public_pool;
  const ptr = el("tr", "pool-row");
  ptr.innerHTML = `<td><b>${esc(pool.name)}</b><br><span class="muted">无责任方的基础设施故障等</span></td>`
    + cats.map((k2) => `<td class="num">${yuan(pool.by_category[k2])}</td>`).join("")
    + `<td class="num"><b>${yuan(pool.total_cents)}</b></td>`
    + `<td class="num">${(pool.total_cents / total * 100).toFixed(1)}%</td>`;
  tb.appendChild(ptr);
  const sum = el("tr", "sum-row");
  sum.innerHTML = "<td>合计（守恒核对）</td>"
    + cats.map((k2) => {
      const v = body.products.reduce((s, p) => s + p.by_category[k2], 0) + pool.by_category[k2];
      return `<td class="num">${yuan(v)}</td>`;
    }).join("")
    + `<td class="num">${yuan(c.allocated_total_cents)}</td><td class="num">100.0%</td>`;
  tb.appendChild(sum);

  // 方案说明
  const m = body.scheme_meta;
  $("#schemeMeta").innerHTML =
    `<p><b>${esc(m.name)}</b></p>
     <p><span class="k">公平性：</span>${esc(m.fairness)}</p>
     <p><span class="k">可追溯性：</span>${esc(m.traceability)}</p>
     <p><span class="k">适用场景：</span>${esc(m.fit)}</p>`;

  // 守恒
  const ok = c.balanced;
  $("#conservationBox").innerHTML = `
    <p class="${ok ? "check-ok" : "check-bad"}">${ok ? "✓ 总额守恒，差额 0 分" : "✗ 守恒失败"}</p>
    <p class="mini-line">有效账单 ${yuan(c.billed_cents)} + 预提 ${yuan(c.accrued_cents)}
       = ${yuan(c.total_cost_cents)}</p>
    <p class="mini-line">产品归集 ${yuan(c.allocated_products_cents)} + 公共池
       ${yuan(c.public_pool_cents)} = ${yuan(c.allocated_total_cents)}</p>
    <p class="muted">逐行最大余数法整数分摊，共享成本不重复全额，失败/投机/重算用量全部保留。</p>`;

  renderLines();
}

function renderLines() {
  const body = state.currentReport.body;
  const filters = $("#lineFilters");
  filters.innerHTML = "";
  ["ALL"].concat(Object.keys(CATEGORY_LABEL)).forEach((k) => {
    const b = el("button", state.lineFilter === k ? "on" : "",
      k === "ALL" ? "全部" : CATEGORY_LABEL[k]);
    b.onclick = () => { state.lineFilter = k; renderLines(); };
    filters.appendChild(b);
  });
  const tb = $("#lineTable tbody");
  tb.innerHTML = "";
  body.lines
    .filter((l) => state.lineFilter === "ALL" || l.category === state.lineFilter)
    .forEach((l) => {
      const tr = el("tr");
      const alloc = Object.entries(l.allocation).map(([k, v]) =>
        `<span class="alloc-pill ${k === "PUBLIC_POOL" ? "pool" : ""}">`
        + `${esc(PRODUCT_NAME[k] || k)} ${yuan(v)}</span>`).join("");
      tr.innerHTML = `
        <td class="mono">${esc(l.attempt_id)}</td>
        <td>${esc(l.task_name)}</td>
        <td>${esc(l.attempt_type)}</td>
        <td>${esc(l.status)}</td>
        <td><span class="badge ${l.category}">${l.category_label}</span></td>
        <td class="num"><b>${yuan(l.cost_cents)}</b></td>
        <td><span class="badge ${l.source}">${l.source === "BILL" ? "真实账单" : "单价预提"}</span>
            ${l.source_id ? `<div class="mono muted">${esc(l.source_id)}</div>` : ""}</td>
        <td>${alloc}<div class="reason">${esc(l.reason)}</div>
            <div class="reason">备注：${esc(l.note)}</div></td>`;
      tb.appendChild(tr);
    });
}

// ------------------------------------------------------------- DAG
function renderDag(snapshot, body) {
  const svg = $("#dagSvg");
  svg.innerHTML = "";
  const NS = "http://www.w3.org/2000/svg";
  const layerX = { 1: 40, 2: 200, 3: 360, 4: 520, 5: 720, 6: 900 };
  const pos = {};
  const byLayer = {};
  snapshot.tasks.forEach((t) => (byLayer[t.layer] = byLayer[t.layer] || []).push(t));
  Object.entries(byLayer).forEach(([layer, tasks]) => {
    tasks.forEach((t, i) => {
      const y = 70 + i * 150 + (3 - tasks.length) * 60;
      pos[t.id] = { x: layerX[layer], y, t };
    });
  });

  const taskCost = {};
  body.tasks.forEach((t) => { taskCost[t.task_id] = t; });
  const edges = snapshot.edges;
  const correctedKeys = new Set();
  if (snapshot.events.some((e) => e.type === "GRAPH_CORRECTION")) {
    correctedKeys.add("t_feature_rec|t_report_bi");
  }

  // 箭头定义（先于边插入，保证引用可见）
  const defs = document.createElementNS(NS, "defs");
  defs.innerHTML = '<marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4" '
    + 'orient="auto"><path d="M0,0 L9,4 L0,8 z" fill="#8aa0b6"/></marker>';
  svg.appendChild(defs);

  // 先画边
  edges.forEach((e) => {
    const a = pos[e.upstream], b = pos[e.downstream];
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "edge " +
      (correctedKeys.has(`${e.upstream}|${e.downstream}`) ? "corrected" : ""));
    const x1 = a.x + 120, y1 = a.y + 26, x2 = b.x, y2 = b.y + 26;
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2 - 8},${y2}`);
    path.setAttribute("marker-end", "url(#arrow)");
    g.appendChild(path);
    const txt = document.createElementNS(NS, "text");
    txt.setAttribute("x", (x1 + x2) / 2);
    txt.setAttribute("y", (y1 + y2) / 2 - 6);
    txt.setAttribute("text-anchor", "middle");
    txt.textContent = "需求 " + e.demand;
    g.appendChild(txt);
    svg.appendChild(g);
  });

  // 节点
  snapshot.tasks.forEach((t) => {
    const p = pos[t.id];
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "node");
    g.setAttribute("transform", `translate(${p.x},${p.y})`);
    g.dataset.task = t.id;
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("width", 130); rect.setAttribute("height", 56);
    rect.setAttribute("rx", 9);
    g.appendChild(rect);
    const name = document.createElementNS(NS, "text");
    name.setAttribute("x", 10); name.setAttribute("y", 22);
    name.setAttribute("class", "name");
    name.textContent = t.name.length > 8 ? t.name.slice(0, 8) + "…" : t.name;
    g.appendChild(name);
    const cost = document.createElementNS(NS, "text");
    cost.setAttribute("x", 10); cost.setAttribute("y", 43);
    cost.setAttribute("class", "cost");
    cost.textContent = yuan(taskCost[t.id]?.total_cents || 0);
    g.appendChild(cost);
    g.onclick = () => showTaskDetail(t.id, snapshot, body, g);
    svg.appendChild(g);
  });
  $("#dagDetail").innerHTML = '<p class="placeholder">点击左侧任务节点查看详情</p>';
}

async function showTaskDetail(taskId, snapshot, body, nodeG) {
  document.querySelectorAll("#dagSvg .node").forEach((n) => n.classList.remove("selected"));
  nodeG.classList.add("selected");
  const task = snapshot.tasks.find((t) => t.id === taskId);
  const taskInfo = body.tasks.find((t) => t.task_id === taskId);
  const attempts = snapshot.attempts.filter((a) => a.task_id === taskId);
  const lines = body.lines.filter((l) => l.task_id === taskId);
  const bills = snapshot.bills.filter((b) =>
    attempts.some((a) => a.id === b.attempt_id));

  const panel = $("#dagDetail");
  panel.innerHTML = "";
  panel.appendChild(el("h3", null, esc(task.name)));
  panel.appendChild(el("p", "mono", esc(task.id)));
  panel.appendChild(el("p", null, `<b>受益产品：</b>${
    taskInfo ? taskInfo.beneficiaries.map((p) => esc(PRODUCT_NAME[p] || p)).join("、") : "—"}`));
  panel.appendChild(el("p", null, `<b>任务总成本：</b>${yuan(taskInfo?.total_cents || 0)}`));

  const catTbl = el("table", "mini-table");
  catTbl.innerHTML = Object.entries(taskInfo?.categories || {}).map(([k, v]) =>
    `<tr><td>${CATEGORY_LABEL[k]}</td><td class="num">${yuan(v)}</td></tr>`).join("");
  panel.appendChild(catTbl);

  panel.appendChild(el("h3", null, "执行尝试与账单"));
  lines.forEach((l) => {
    const card = el("div", "card");
    card.style.marginBottom = "8px";
    const alloc = Object.entries(l.allocation).map(([k, v]) =>
      `<span class="alloc-pill ${k === "PUBLIC_POOL" ? "pool" : ""}">`
      + `${esc(PRODUCT_NAME[k] || k)} ${yuan(v)}</span>`).join("");
    card.innerHTML = `
      <div class="mono">${esc(l.attempt_id)} · ${esc(l.attempt_type)} · ${esc(l.status)}</div>
      <div><span class="badge ${l.category}">${l.category_label}</span>
           <span class="badge ${l.source}">${l.source === "BILL" ? "真实账单" : "单价预提"}</span>
           <b style="float:right">${yuan(l.cost_cents)}</b></div>
      <div style="margin-top:6px">${alloc}</div>
      <div class="reason">${esc(l.reason)}</div>`;
    panel.appendChild(card);
  });

  if (bills.length) {
    panel.appendChild(el("h3", null, "账单证据（含替代关系）"));
    bills.forEach((b) => {
      panel.appendChild(el("p", "reason",
        `<span class="mono">${esc(b.id)}</span> · ${esc(b.status)} · ${yuan(b.amount_cents)}`
        + (b.supersedes ? ` · 替代 <span class="mono">${esc(b.supersedes)}</span>` : "")
        + `<br>${esc(b.note)}`));
    });
  }
}

// ------------------------------------------------------------- 方案比较
async function renderCompare() {
  const data = await api("/api/compare-schemes?version=" + state.currentVersion);
  const schemes = ["OCCUPANCY", "MARGINAL", "CAUSAL"];
  const table = $("#compareTable");
  table.querySelector("thead").innerHTML = "<tr><th>归集对象</th>"
    + schemes.map((s) => `<th class="num">${esc(data.reports[s].scheme_meta.name)}</th>`).join("")
    + '<th class="num">最大-最小</th></tr>';
  const tb = table.querySelector("tbody");
  tb.innerHTML = "";
  data.rows.forEach((r) => {
    const vals = schemes.map((s) => r.by_scheme[s]);
    const spread = Math.max(...vals) - Math.min(...vals);
    const tr = el("tr", r.key === "PUBLIC_POOL" ? "pool-row" : "");
    tr.innerHTML = `<td><b>${esc(r.name)}</b><br><span class="muted">${esc(r.key)}</span></td>`
      + vals.map((v) => `<td class="num">${yuan(v)}</td>`).join("")
      + `<td class="num ${spread ? "up" : ""}">${yuan(spread)}</td>`;
    tb.appendChild(tr);
  });

  $("#schemeCompareCards").innerHTML = "";
  schemes.forEach((s) => {
    const m = data.reports[s].scheme_meta;
    const card = el("div", "card");
    card.innerHTML = `<h3>${esc(m.name)}</h3>
      <p><span class="k">公平性：</span>${esc(m.fairness)}</p>
      <p><span class="k">取舍：</span>${esc(m.fit)}</p>`;
    $("#schemeCompareCards").appendChild(card);
  });
}

// ------------------------------------------------------------- 修订追溯
function renderRevisions() {
  const tl = $("#timeline");
  tl.innerHTML = "";
  state.evidence.forEach((ev) => {
    const item = el("div", "tl-item");
    item.innerHTML = `<div class="tl-ver">${esc(ev.version)}
        <span class="hash-chip">${shortHash(ev.evidence_hash)}</span></div>
      <div class="muted">${esc(ev.description)}</div>`;
    ev.events.forEach((e2) => {
      item.appendChild(el("div", "tl-event",
        `· <b>${esc(e2.type)}</b>（${esc(e2.time)}）：${esc(e2.summary)}`));
    });
    tl.appendChild(item);
  });

  // diff 下拉
  const all = state.reports;
  ["diffA", "diffB"].forEach((id, idx) => {
    const sel = $("#" + id);
    sel.innerHTML = "";
    all.forEach((r) => {
      const o = el("option", null,
        `${r.report_id} [${r.status}] ${shortHash(r.report_hash)}`);
      o.value = r.report_id;
      sel.appendChild(o);
    });
    if (id === "diffA") sel.value = "RPT-EV1-OCCUPANCY";
    if (id === "diffB") sel.value = "RPT-EV3-OCCUPANCY";
  });

  // 台账
  api("/api/ledger").then((d) => {
    const tb = $("#ledgerTable tbody");
    tb.innerHTML = "";
    d.items.forEach((e2) => {
      const tr = el("tr");
      tr.innerHTML = `<td class="num">${e2.seq}</td><td class="mono">${esc(e2.time)}</td>
        <td><b>${esc(e2.type)}</b></td><td class="mono" style="max-width:420px;overflow-wrap:anywhere">
        ${esc(JSON.stringify(e2.payload))}</td>
        <td class="mono">${shortHash(e2.hash)}<br><span class="muted">← ${shortHash(e2.prev_hash)}</span></td>`;
      tb.appendChild(tr);
    });
  });
}

async function runDiff() {
  const a = $("#diffA").value, b = $("#diffB").value;
  const d = await api(`/api/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  $("#diffTable").querySelector("thead").innerHTML =
    `<tr><th>归集对象</th><th class="num">A：${esc(d.a.report_id)} (${d.a.version}/${d.a.scheme})</th>
     <th class="num">B：${esc(d.b.report_id)} (${d.b.version}/${d.b.scheme})</th>
     <th class="num">差额（B−A）</th></tr>`;
  const tb = $("#diffTable tbody");
  tb.innerHTML = "";
  d.rows.forEach((r) => {
    const cls = r.diff_cents > 0 ? "up" : r.diff_cents < 0 ? "down" : "";
    const tr = el("tr", r.key === "PUBLIC_POOL" ? "pool-row" : "");
    tr.innerHTML = `<td><b>${esc(r.name)}</b></td>
      <td class="num">${yuan(r.total_a)}</td><td class="num">${yuan(r.total_b)}</td>
      <td class="num ${cls}"><b>${r.diff_cents > 0 ? "+" : ""}${yuan(r.diff_cents)}</b></td>`;
    tb.appendChild(tr);
  });
  const sum = el("tr", "sum-row");
  sum.innerHTML = `<td>成本总额</td><td class="num">${yuan(d.total_a)}</td>
    <td class="num">${yuan(d.total_b)}</td>
    <td class="num ${d.total_diff ? "up" : ""}"><b>+${yuan(d.total_diff)}</b></td>`;
  tb.appendChild(sum);
}

function renderConfirmRow() {
  const row = $("#confirmRow");
  row.innerHTML = "";
  state.reports
    .filter((r) => r.body.evidence_version === state.currentVersion)
    .forEach((r) => {
      const card = el("div", "confirm-card");
      card.innerHTML = `<b>${esc(r.report_id)}</b>
        <span class="badge ${r.status}">${r.status === "CONFIRMED" ? "已确认（不可回退）" : "草稿"}</span>
        <span class="mono">hash ${shortHash(r.report_hash)}</span>
        <span class="muted">绑定证据 ${shortHash(r.body.evidence_hash)}</span>`;
      if (r.status === "DRAFT") {
        const btn = el("button", "primary", "确认这份报告");
        btn.onclick = async () => {
          btn.disabled = true;
          try {
            await api("/api/report/confirm", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ id: r.report_id, note: "网页端确认" }),
            });
            await refreshAll();
          } catch (e2) {
            alert(e2.message);
            btn.disabled = false;
          }
        };
        card.appendChild(btn);
      }
      row.appendChild(card);
    });
}

// ------------------------------------------------------------- 静态事件
function bindStaticEvents() {
  $("#evSelect").onchange = async (e) => {
    state.currentVersion = e.target.value;
    state.lineFilter = "ALL";
    await loadVersion();
  };
  $("#schemeSelect").onchange = async (e) => {
    state.currentScheme = e.target.value;
    await loadVersion();
  };
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      $("#tab-" + t.dataset.tab).classList.add("active");
    };
  });
  $("#btnDiff").onclick = () => runDiff().catch((e) => alert(e.message));
  $("#btnVerify").onclick = async () => {
    const v = await api("/api/verify");
    const banner = $("#verifyBanner");
    banner.hidden = false;
    banner.className = "verify-banner " + (v.ok ? "ok" : "bad");
    banner.innerHTML = v.ok
      ? `✓ 完整性校验通过：${v.evidence_count} 版证据、${v.ledger_entries} 条台账，哈希链完整，全部报告可由固定证据复现且守恒。`
      : "✗ 发现问题：" + v.problems.map(esc).join("；");
  };
}

init().catch((e) => {
  document.body.insertAdjacentHTML("afterbegin",
    `<div class="verify-banner bad">初始化失败：${esc(e.message)}（请先运行 python3 run.py seed）</div>`);
});
