/* 看板主逻辑：加载数据 -> 渲染 KPI / 三大趋势图 / AI 解读 / 关联洞察。
   本地服务不可用时自动回退到内置的离线演示数据（demo-data.js）。 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s === undefined || s === null ? "" : s)
    .replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d) => (v === null || v === undefined || !isFinite(v) ? "—" : Chart.num(v, d === undefined ? 1 : d));
  const text2html = (s) => esc(s).replace(/\n/g, "<br>");

  const state = { days: 30, data: null, offline: false, busy: false };

  const C = { sleep: "#6d5ce7", hrv: "#0d9488", cal: "#f59e0b", accent: "#4f46e5", warn: "#e07a3f" };

  const KPIS = [
    { key: "sleep_score", label: "睡眠评分", unit: "分", color: "#6d5ce7", soft: "#f0edff", icon: "☾", d: 0, better: "up" },
    { key: "sleep_duration", label: "平均睡眠时长", unit: "小时", color: "#8b83f8", soft: "#f0edff", icon: "◐", d: 1, better: "up" },
    { key: "hrv", label: "夜间 HRV", unit: "ms", color: "#0d9488", soft: "#e5f6f3", icon: "∿", d: 1, better: "up" },
    { key: "calories_total", label: "日均总消耗", unit: "kcal", color: "#f59e0b", soft: "#fff4e1", icon: "▲", d: 0, better: "neutral" },
  ];

  let toastTimer = null;
  function toast(msg, ms) {
    const el = $("toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, ms || 3600);
  }

  async function load(days, refresh) {
    if (state.busy) return;
    state.busy = true;
    state.days = days || state.days;
    $("btn-refresh").disabled = true;
    $("source-badge").className = "badge badge-loading";
    $("source-badge").textContent = "加载中…";
    try {
      const url = "/api/dashboard?days=" + state.days + (refresh ? "&refresh=1" : "");
      const resp = await fetch(url, { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      state.data = await resp.json();
      state.offline = false;
    } catch (err) {
      const demo = (window.DEMO_DATA || {})[String(state.days)] || (window.DEMO_DATA || {})["30"];
      if (!demo) {
        $("notice").hidden = false;
        $("notice").className = "notice error";
        $("notice").innerHTML = "<b>无法加载数据</b>：本地服务未启动，且内置演示数据缺失。请运行 <code>python server.py</code> 后访问 http://127.0.0.1:8765";
        state.busy = false;
        $("btn-refresh").disabled = false;
        return;
      }
      state.data = demo;
      state.offline = true;
    }
    state.busy = false;
    $("btn-refresh").disabled = false;
    document.querySelectorAll("#range-tabs button").forEach((b) => {
      b.classList.toggle("active", Number(b.dataset.days) === state.days);
    });
    render();
    if (refresh) toast("数据已刷新");
  }

  function render() {
    if (!state.data) return;
    renderHeader();
    renderKPI();
    renderCharts();
    renderAI();
    renderSide();
    renderSetup();
  }

  function renderHeader() {
    const cfg = state.data.config || {}, meta = state.data.meta || {};
    const badge = $("source-badge");
    if (state.offline || meta.offline) {
      badge.className = "badge badge-offline"; badge.textContent = "离线演示";
    } else if (meta.source === "garmin") {
      badge.className = "badge badge-live"; badge.textContent = "Garmin 实时数据";
    } else {
      badge.className = "badge badge-demo"; badge.textContent = "演示数据";
    }

    const notes = [];
    if (meta.fallback_reason) notes.push(esc(meta.fallback_reason));
    if (meta.errors && Object.keys(meta.errors).length) {
      const auth = meta.errors.auth;
      if (auth) {
        notes.push("<b>Garmin 认证阶段失败</b>：" + esc(auth.message || "token 交换失败") +
          (auth.status ? " (HTTP " + esc(auth.status) + ")" : "") +
          (auth.hint ? " · " + esc(auth.hint) : "") +
          "。请在「设置」里核对 Key 与地区，再用「测试 Garmin」验证；认证失败时不会发起逐日降级。");
      }
      const endpointErrors = Object.entries(meta.errors).filter(([k]) => k !== "auth");
      if (endpointErrors.length) {
        const parts = endpointErrors.map(([k, v]) => `${k}: ${v && v.message ? v.message : "请求失败"}${v && v.status ? " (HTTP " + v.status + ")" : ""}`);
        notes.push("部分端点不可用，已尝试逐日降级：<b>" + esc(parts.join("；")) + "</b>。可在「数据与字段」中查看原始响应核对参数名。");
      }
    }
    if (meta.fallbacks && meta.fallbacks.length) notes.push("已自动降级为逐日拉取的端点：" + esc(meta.fallbacks.join("、")));
    if (!cfg.deepseek_configured && !state.offline) notes.push("未配置 <b>DEEPSEEK_API_KEY</b>，当前 AI 解读使用本地统计规则；点击右上角「设置」填写 Key 保存后即时生效。");
    const box = $("notice");
    if (notes.length) {
      box.hidden = false;
      box.className = "notice" + (meta.errors && Object.keys(meta.errors).length ? " error" : " info");
      box.innerHTML = notes.map((n) => "<div>" + n + "</div>").join("");
    } else {
      box.hidden = true;
    }
  }

  function changeInfo(m) {
    const c = m.change_pct;
    if (c === null || c === undefined) return { cls: "change-flat", txt: "无环比" };
    const up = c > 0.05, down = c < -0.05;
    const arrow = up ? "↑" : down ? "↓" : "→";
    let cls = "change-flat";
    if (m.better === "up") cls = up ? "change-up" : down ? "change-down" : "change-flat";
    else if (m.better === "down") cls = up ? "change-down" : down ? "change-up" : "change-flat";
    return { cls, txt: arrow + " " + Math.abs(c).toFixed(1) + "%" };
  }

  function renderKPI() {
    const metrics = (state.data.analysis || {}).metrics || {};
    $("kpi-grid").innerHTML = KPIS.map((k) => {
      const m = metrics[k.key];
      if (!m) return "";
      const ch = changeInfo(m);
      return `<div class="kpi-card" style="--kpi-color:${k.color};--kpi-soft:${k.soft}">
        <div class="kpi-head"><span class="kpi-label">${esc(k.label)}</span><span class="kpi-icon">${esc(k.icon)}</span></div>
        <div class="kpi-main"><span class="kpi-value">${fmt(m.avg7, k.d)}</span><span class="kpi-unit">${esc(k.unit)}</span></div>
        <div class="kpi-meta"><span class="kpi-change ${ch.cls}">${ch.txt}</span><span class="kpi-note">近 7 日均值 · 对比前 7 日</span></div>
        <div class="kpi-spark" data-spark="${k.key}"></div>
      </div>`;
    }).join("");
    KPIS.forEach((k) => {
      const m = metrics[k.key];
      if (!m) return;
      const data = m.series.map((x) => x.value).slice(-Math.min(45, state.days));
      Chart.sparkline(document.querySelector(`[data-spark="${k.key}"]`), data, k.color);
    });
  }

  function renderCharts() {
    const a = state.data.analysis || {};
    const recs = a.records || [];
    const m = a.metrics || {};
    const labels = recs.map((r) => r.date);
    const n = recs.length;
    const pick = (fn) => recs.map((r) => { const v = fn(r); return v === null || v === undefined || !isFinite(v) ? null : Number(v); });
    const dots = n <= 45;

    // ---------- 睡眠 ----------
    const sAvg = m.sleep_duration, sScore = m.sleep_score, deep = m.deep_ratio;
    const maxSleepMin = Math.max.apply(null, recs.map((r) => r.sleep.duration_min || 0).concat([480]));
    const sleepAxisMax = Math.ceil(maxSleepMin / 120) * 120;
    $("sleep-sub").textContent = `近 7 日平均 ${fmt(sAvg && sAvg.avg7, 1)} 小时 · 评分 ${fmt(sScore && sScore.avg7, 0)} 分 · ${sScore ? sScore.covered : 0}/${n} 天有记录`;
    Chart.render($("chart-sleep"), {
      uid: "sleep", title: "睡眠结构与评分", height: 268, labels,
      left: { min: 0, max: sleepAxisMax, step: 120, format: (v) => (v / 60).toFixed(0) + "h" },
      right: { min: 0, max: 100, ticks: 4, format: (v) => Chart.num(v, 0) },
      series: [
        { name: "深睡", type: "bar", stack: "sleep", data: pick((r) => r.sleep.deep_min), color: "#5b4bd6", unit: " 分", decimals: 0 },
        { name: "浅睡", type: "bar", stack: "sleep", data: pick((r) => r.sleep.light_min), color: "#a99cfb", unit: " 分", decimals: 0 },
        { name: "REM", type: "bar", stack: "sleep", data: pick((r) => r.sleep.rem_min), color: "#c9c1fd", unit: " 分", decimals: 0 },
        { name: "清醒", type: "bar", stack: "sleep", data: pick((r) => r.sleep.awake_min), color: "#e6e2fb", unit: " 分", decimals: 0 },
        { name: "睡眠评分", type: "line", axis: "right", data: pick((r) => r.sleep.score), color: C.cal, width: 2.2, dots: dots, unit: " 分", decimals: 0, format: (v) => fmt(v, 0) },
      ],
    });
    const remRatio = recs.reduce((acc, r) => {
      const d = r.sleep.duration_min, v = r.sleep.rem_min;
      return d && v ? { sum: acc.sum + v / d * 100, n: acc.n + 1 } : acc;
    }, { sum: 0, n: 0 });
    $("sleep-foot").innerHTML = [
      `深睡占比均值 <b>${fmt(deep && deep.avg7, 1)}%</b>`,
      `REM 占比均值 <b>${fmt(remRatio.n ? remRatio.sum / remRatio.n : null, 1)}%</b>`,
      `最长 / 最短 <b>${fmt(sAvg && sAvg.max, 1)} / ${fmt(sAvg && sAvg.min, 1)} h</b>`,
      `整体趋势 <b>${esc((sAvg && sAvg.trend) || "—")}</b>`,
    ].map((x) => `<span class="foot-item">${x}</span>`).join("");

    // ---------- HRV ----------
    const h = m.hrv || {};
    const low = recs.find((r) => r.hrv.baseline_low !== null)?.hrv.baseline_low;
    const high = recs.find((r) => r.hrv.baseline_high !== null)?.hrv.baseline_high;
    const bands = (low !== null && low !== undefined && high !== null && high !== undefined)
      ? [{ from: low, to: high, color: C.hrv, label: "个人基线区间" }] : [];
    const belowCount = recs.filter((r) => r.hrv.nightly_avg !== null && low !== null && low !== undefined && r.hrv.nightly_avg < low).length;
    // 截断 Y 轴到数据实际区间，避免 HRV 波动被 0 基线压平
    const hrvVals = recs.map((r) => r.hrv.nightly_avg).filter((v) => v !== null && v !== undefined && isFinite(v));
    const loAll = Math.min.apply(null, hrvVals.concat(low === null || low === undefined ? [] : [low]));
    const hiAll = Math.max.apply(null, hrvVals.concat(high === null || high === undefined ? [] : [high]));
    const yMin = hrvVals.length ? Math.max(0, Math.floor((loAll - 8) / 5) * 5) : 0;
    const yMax = hrvVals.length ? Math.ceil((hiAll + 8) / 5) * 5 : 100;
    $("hrv-sub").textContent = `近 7 日均值 ${fmt(h.avg7, 1)} ms · 趋势${h.trend || "不明确"} · 基线 ${fmt(low, 0)}–${fmt(high, 0)} ms`;
    Chart.render($("chart-hrv"), {
      uid: "hrv", title: "心率变异性趋势", height: 254, labels, bands,
      hlines: h.avg7 ? [{ value: h.avg7, color: C.cal, label: "近 7 日均值" }] : [],
      left: { min: yMin, max: yMax, format: (v) => Chart.num(v, 0) },
      series: [
        { name: "夜间 HRV", type: "line", data: pick((r) => r.hrv.nightly_avg), color: C.hrv, width: 2.2, fill: true, fillOpacity: 0.09, dots: dots, unit: " ms", decimals: 1 },
        { name: "7 日均线", type: "line", data: (h.ma7 || []).map((v) => v === null || v === undefined ? null : Number(v)), color: "#5ec9bd", width: 1.9, dash: true, dots: false, unit: " ms", decimals: 1 },
      ],
    });
    $("hrv-foot").innerHTML = [
      `区间 <b>${fmt(h.min, 0)}–${fmt(h.max, 0)} ms</b>`,
      `低于基线 <b>${belowCount} 天</b>`,
      `环比 <b>${h.change_pct === null || h.change_pct === undefined ? "—" : (h.change_pct > 0 ? "+" : "") + h.change_pct.toFixed(1) + "%"}</b>`,
      `有效记录 <b>${h.covered || 0}/${n} 天</b>`,
    ].map((x) => `<span class="foot-item">${x}</span>`).join("");

    // ---------- 卡路里 ----------
    const ct = m.calories_total || {}, ca = m.calories_active || {}, steps = m.steps || {};
    $("cal-sub").textContent = `日均总消耗 ${fmt(ct.avg7, 0)} kcal · 活动消耗 ${fmt(ca.avg7, 0)} kcal · ${ca.change_pct === null || ca.change_pct === undefined ? "环比暂无" : "环比 " + (ca.change_pct > 0 ? "+" : "") + ca.change_pct.toFixed(1) + "%"}`;
    Chart.render($("chart-cal"), {
      uid: "cal", title: "卡路里消耗构成", height: 254, labels,
      left: { format: (v) => (v >= 1000 ? (v / 1000).toFixed(1) + "k" : Chart.num(v, 0)) },
      series: [
        { name: "基础代谢", type: "bar", stack: "cal", data: pick((r) => r.calories.bmr), color: "#fcd56a", unit: " kcal", decimals: 0 },
        { name: "活动消耗", type: "bar", stack: "cal", data: pick((r) => r.calories.active), color: "#f2a01a", unit: " kcal", decimals: 0 },
        { name: "活动 7 日均线", type: "line", data: (ca.ma7 || []).map((v) => v === null || v === undefined ? null : Number(v)), color: "#d9662e", width: 2, dash: true, dots: false, unit: " kcal", decimals: 0 },
      ],
    });
    const ratio = ct.avg7 && ca.avg7 ? ca.avg7 / ct.avg7 * 100 : null;
    $("cal-foot").innerHTML = [
      `日均步数 <b>${fmt(steps.avg7, 0)}</b>`,
      `活动消耗占比 <b>${fmt(ratio, 0)}%</b>`,
      `总消耗趋势 <b>${esc(ct.trend || "—")}</b>`,
      `最高单日 <b>${fmt(ct.max, 0)} kcal</b>`,
    ].map((x) => `<span class="foot-item">${x}</span>`).join("");
  }

  function renderAI() {
    const interp = state.data.interpretation || {};
    const sections = interp.sections || {};
    const blocks = [
      { key: "sleep", title: "睡眠趋势", color: C.sleep },
      { key: "hrv", title: "HRV 与恢复", color: C.hrv },
      { key: "calories", title: "能量消耗", color: C.cal },
      { key: "overall", title: "综合建议", color: C.accent, overall: true },
    ];
    $("ai-highlights").innerHTML = (interp.highlights || []).length
      ? interp.highlights.map((h) => `<span class="chip">${esc(h)}</span>`).join("")
      : "";
    $("ai-sections").innerHTML = blocks.map((b) =>
      `<div class="ai-block ${b.overall ? "overall" : ""}">
         <h3><span class="dot" style="background:${b.color}"></span>${esc(b.title)}</h3>
         <p>${text2html(sections[b.key] || "暂无该部分解读。")}</p>
       </div>`
    ).join("");
    const sourceText = interp.source === "deepseek" || interp.source === "cache"
      ? `DeepSeek · ${esc(interp.model || "deepseek-flash")}${interp.from_cache ? " · 命中缓存" : ""}`
      : "本地统计规则解读";
    const t = interp.generated_at ? new Date(interp.generated_at).toLocaleString("zh-CN") : "—";
    $("ai-sub").textContent = `基于当前 ${state.days} 天范围的聚合统计生成 · ${sourceText}`;
    $("ai-foot").innerHTML =
      `<span>解读来源：<b>${sourceText}</b></span><span>生成时间：${esc(t)}</span>` +
      (interp.warning ? `<span class="warn-text">${esc(interp.warning)}</span>` : "") +
      `<span>注：仅发送聚合统计，不发送原始健康明细或任何凭证。</span>`;
  }

  function renderSide() {
    const a = state.data.analysis || {};
    const meta = state.data.meta || {};
    const corr = a.correlations || [];
    $("correlations").innerHTML = corr.length ? corr.map((c) =>
      `<div class="corr-row">
         <div class="corr-top"><span class="corr-label">${esc(c.label)}</span>
           <span class="corr-r" style="color:${c.r >= 0 ? C.hrv : C.warn}">r = ${c.r.toFixed(2)}</span></div>
         <div class="corr-bar"><div class="corr-bar-fill" style="width:${Math.min(100, Math.abs(c.r) * 100).toFixed(0)}%"></div></div>
         <div class="corr-note">${esc(c.strength)}${esc(c.direction)} · ${c.n} 组有效配对</div>
       </div>`
    ).join("") : `<div class="empty">有效配对样本不足，暂无法计算相关性。</div>`;

    const anomalies = a.anomalies || [];
    $("anomalies").innerHTML = anomalies.length ? anomalies.slice(0, 7).map((x) =>
      `<div class="anomaly-row">
         <span class="anomaly-date">${esc(x.date)}</span>
         <span class="anomaly-metric">${esc(x.metric)} ${esc(x.direction)}</span>
         <span class="anomaly-value ${x.direction === "偏低" ? "low" : "high"}">${fmt(x.value, 1)} ${esc(x.unit)}</span>
       </div>`
    ).join("") : `<div class="empty">当前范围内没有明显偏离均值的异常日。</div>`;

    const cov = (meta.coverage && meta.coverage.metrics) || {};
    const names = {
      sleep_score: "睡眠评分", sleep_duration: "睡眠时长", sleep_deep: "深睡时长", sleep_rem: "REM 时长",
      hrv: "夜间 HRV", hrv_baseline_low: "HRV 基线下限", hrv_baseline_high: "HRV 基线上限",
      calories_total: "总消耗", calories_active: "活动消耗", calories_bmr: "基础代谢", steps: "步数",
    };
    const keys = Object.keys(cov).length ? Object.keys(cov) : Object.keys(names);
    const total = meta.days || state.days;
    $("coverage").innerHTML = keys.map((k) => {
      const c = cov[k] === undefined ? null : cov[k];
      const pct = c === null ? 100 : Math.min(100, c / total * 100);
      return `<div class="cov-row"><span>${esc(names[k] || k)}</span><span>${c === null ? "演示数据" : c + "/" + total + " 天"}</span></div>
              <div class="cov-bar"><i style="width:${pct.toFixed(0)}%"></i></div>`;
    }).join("") + `<p class="fine">数据源：<b>${esc(meta.source === "garmin" ? "Garmin Personal API" : "内置演示数据")}</b>` +
      (meta.region ? ` · 地区主机 ${esc(meta.region)}` : "") +
      (meta.fetched_at ? `<br>抓取时间 ${esc(new Date(meta.fetched_at).toLocaleString("zh-CN"))}` : "") + `</p>`;
  }

  function renderSetup() {
    const cfg = state.data.config || {}, guide = state.data.guide || {};
    const show = !cfg.garmin_configured || !cfg.deepseek_configured;
    $("setup-card").hidden = !show;
    if (!show) return;
    $("setup-steps").innerHTML = (guide.steps || []).map((s) => `<li>${esc(s)}</li>`).join("");
    const links = [
      ["Garmin Personal API Key（全球区）", "https://connect.garmin.com/app/settings/personalApiToken"],
      ["Garmin Personal API Key（中国区）", "https://connect.garmin.cn/app/settings/personalApiToken"],
      ["官方 Personal API FAQ", guide.faq_url],
      ["官方 Swagger 端点文档", guide.swagger_url],
      ["DeepSeek API Keys", guide.deepseek_keys_url],
    ].filter((x) => x[1]);
    $("setup-links").innerHTML = links.map(([name, url]) =>
      `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(name)} ↗</a>`
    ).join("");
  }

  // ---------------- 设置面板 ----------------
  const settingsState = { loaded: false, initial: null, meta: null, clearMark: "__CLEAR__" };
  const SETTING_FIELDS = [
    ["cfg-region", "GARMIN_REGION"],
    ["cfg-garmin-base", "GARMIN_API_BASE"],
    ["cfg-deepseek-base", "DEEPSEEK_BASE_URL"],
    ["cfg-deepseek-model", "DEEPSEEK_MODEL"],
    ["cfg-data-source", "DATA_SOURCE"],
    ["cfg-default-days", "DEFAULT_DAYS"],
    ["cfg-cache-ttl", "CACHE_TTL_MINUTES"],
    ["cfg-timeout", "HTTP_TIMEOUT"],
  ];
  const SECRET_FIELDS = [
    ["cfg-garmin-key", "GARMIN_PERSONAL_API_KEY", "garmin-key-state"],
    ["cfg-deepseek-key", "DEEPSEEK_API_KEY", "deepseek-key-state"],
  ];

  function settingsMsg(text, kind) {
    const el = $("settings-msg");
    if (!text) { el.hidden = true; return; }
    el.hidden = false;
    el.className = "settings-msg " + (kind || "pending");
    el.textContent = text;
  }

  function updateRegionHint() {
    const region = $("cfg-region").value;
    const meta = settingsState.meta || {};
    const found = (meta.regions || []).find((x) => x.value === region);
    const custom = $("cfg-garmin-base").value.trim();
    $("region-hint").innerHTML = custom
      ? "当前使用自定义主机 <b>" + esc(custom) + "</b>；清空则恢复按地区自动选择。"
      : "当前使用官方主机 <b>" + esc(found ? found.base : "—") + "</b>；中国区账号请选择「中国区」。";
    $("link-garmin-key").href = region === "cn"
      ? "https://connect.garmin.cn/app/settings/personalApiToken"
      : "https://connect.garmin.com/app/settings/personalApiToken";
  }

  function fillSettings(j) {
    const s = j.settings || {}, v = s.values || {};
    settingsState.meta = s;
    settingsState.clearMark = s.clear_mark || "__CLEAR__";
    $("cfg-region").innerHTML = (s.regions || [])
      .map((r) => `<option value="${esc(r.value)}">${esc(r.label)}</option>`).join("");
    $("model-choices").innerHTML = (s.models || [])
      .map((m) => `<option value="${esc(m.value)}" label="${esc(m.label)}"></option>`).join("");
    const set = (id, val) => { $(id).value = (val === null || val === undefined) ? "" : String(val); };
    SETTING_FIELDS.forEach(([id, key]) => set(id, v[key]));
    $("cfg-deepseek-thinking").checked = !!v.DEEPSEEK_THINKING;
    $("cfg-port").textContent = s.port || 8765;
    $("settings-note").textContent = "配置保存在项目根目录 .env · 密钥只写入本机，页面与 API 响应都不回显明文。";
    $("settings-note").title = "配置文件：" + (s.env_path || ".env");
    SECRET_FIELDS.forEach(([id, key, stateId]) => {
      const el = $(id), info = (s.secrets || {})[key] || {};
      el.value = ""; el.disabled = false; el.dataset.clear = "0"; el.placeholder = "留空保持不变";
      $(stateId).textContent = info.configured ? (info.hint ? "当前 " + info.hint : "已配置") : "未配置";
    });
    document.querySelectorAll("[data-clear]").forEach((b) => { b.textContent = "清除"; });
    updateRegionHint();
  }

  function collectSettings() {
    const full = {};
    SETTING_FIELDS.forEach(([id, key]) => { full[key] = $(id).value.trim(); });
    full.DEEPSEEK_THINKING = $("cfg-deepseek-thinking").checked ? "1" : "0";
    // 自定义主机留空即表示「清除覆盖，按地区自动」
    if (!full.GARMIN_API_BASE) full.GARMIN_API_BASE = settingsState.clearMark;
    SECRET_FIELDS.forEach(([id, key]) => {
      const el = $(id);
      if (el.dataset.clear === "1") full[key] = settingsState.clearMark;
      else if (el.value.trim()) full[key] = el.value.trim();
    });
    return full;
  }

  function settingsDiff() {
    const full = collectSettings(), initial = settingsState.initial || {}, diff = {};
    Object.keys(full).forEach((k) => {
      const before = initial[k] === undefined ? "" : String(initial[k]);
      if (before !== String(full[k])) diff[k] = full[k];
    });
    return diff;
  }

  async function openSettings() {
    if (state.offline) { toast("离线预览模式无法保存配置，请先通过桌面图标启动本地服务"); return; }
    $("settings-modal").hidden = false;
    settingsState.loaded = false;
    settingsMsg("正在读取本机配置…", "pending");
    try {
      const r = await fetch("/api/settings", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();
      fillSettings(j);
      settingsState.initial = collectSettings();
      settingsState.loaded = true;
      const s = j.settings || {};
      settingsMsg(s.env_writable ? "" : "配置文件不可写，保存会失败，请检查文件权限。",
        s.env_writable ? "" : "error");
    } catch (e) {
      settingsMsg("读取配置失败：" + e.message, "error");
    }
  }

  function closeSettings() { $("settings-modal").hidden = true; }

  async function saveSettings(ev) {
    ev.preventDefault();
    if (!settingsState.loaded) return;
    const diff = settingsDiff();
    if (!Object.keys(diff).length) {
      closeSettings();
      toast("配置没有变化，已按当前配置重新刷新数据");
      load(state.days, true);
      return;
    }
    const btn = $("btn-settings-save");
    btn.disabled = true; btn.textContent = "保存中…";
    settingsMsg("正在保存并重新拉取数据…", "pending");
    try {
      const r = await fetch("/api/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: diff }),
      });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.message || ("HTTP " + r.status));
      closeSettings();
      settingsState.loaded = false;
      toast(j.message || "配置已保存并生效");
      await load(state.days, true);
    } catch (e) {
      settingsMsg("保存失败：" + e.message, "error");
    }
    btn.disabled = false; btn.textContent = "保存并刷新数据";
  }

  async function testFromSettings(target) {
    if (state.offline) { toast("离线模式无法测试连接"); return; }
    if (Object.keys(settingsDiff()).length) {
      settingsMsg("有未保存的修改，请先点「保存并刷新数据」再测试。", "pending");
      return;
    }
    const label = target === "deepseek" ? "DeepSeek" : "Garmin";
    settingsMsg("正在验证 " + label + " 接口连通性…", "pending");
    try {
      const r = await fetch("/api/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target }),
      });
      const j = await r.json();
      const detail = (j.message || "验证结束") + (j.hint ? " · " + j.hint : "");
      settingsMsg((j.ok ? "✓ " : "✗ ") + detail, j.ok ? "ok" : "error");
      toast((j.ok ? "✓ " : "✗ ") + detail, 6500);
    } catch (e) { settingsMsg("测试失败：" + e.message, "error"); }
  }

  $("btn-settings").addEventListener("click", openSettings);
  $("btn-settings-close").addEventListener("click", closeSettings);
  $("settings-modal").addEventListener("click", (e) => { if (e.target === $("settings-modal")) closeSettings(); });
  $("settings-form").addEventListener("submit", saveSettings);
  $("btn-test-garmin").addEventListener("click", () => testFromSettings("garmin"));
  $("btn-test-deepseek").addEventListener("click", () => testFromSettings("deepseek"));
  $("cfg-region").addEventListener("change", () => {
    if ($("cfg-garmin-base").value.trim()) $("cfg-garmin-base").value = "";
    updateRegionHint();
  });
  $("cfg-garmin-base").addEventListener("input", updateRegionHint);
  document.querySelectorAll("[data-clear]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const el = $(btn.dataset.clear);
      if (el.dataset.clear === "1") {
        el.dataset.clear = "0"; el.disabled = false;
        el.placeholder = "留空保持不变"; btn.textContent = "清除";
      } else {
        el.dataset.clear = "1"; el.value = ""; el.disabled = true;
        el.placeholder = "保存时将清除已配置的 Key"; btn.textContent = "撤销";
      }
    });
  });

  // ---------------- 交互 ----------------
  $("range-tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-days]");
    if (btn) load(Number(btn.dataset.days), false);
  });
  $("btn-refresh").addEventListener("click", () => load(state.days, true));

  $("btn-test").addEventListener("click", async () => {
    if (state.offline) { toast("离线演示模式下无法测试连接，请先启动本地服务"); return; }
    toast("正在验证 Garmin token 交换与只读访问…");
    try {
      const r = await fetch("/api/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const j = await r.json();
      toast((j.ok ? "✓ " : "✗ ") + (j.message || "验证结束") + (j.hint ? " · " + j.hint : ""), 6500);
    } catch (e) { toast("测试请求失败：" + e.message); }
  });

  $("btn-reinterpret").addEventListener("click", async () => {
    if (state.offline) { toast("离线演示模式下无法调用大模型，请启动本地服务并配置 DEEPSEEK_API_KEY"); return; }
    const btn = $("btn-reinterpret");
    btn.disabled = true; btn.textContent = "生成中…";
    try {
      const r = await fetch("/api/interpret", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: state.days }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();
      state.data.interpretation = j.interpretation;
      renderAI();
      toast(j.interpretation.source === "deepseek" ? "DeepSeek 解读已更新" : (j.interpretation.warning || "已使用规则化解读"));
    } catch (e) { toast("生成失败：" + e.message); }
    btn.disabled = false; btn.textContent = "重新生成解读";
  });

  $("btn-raw").addEventListener("click", async () => {
    if (state.offline) { toast("离线演示模式没有原始 API 响应"); return; }
    $("raw-modal").hidden = false;
    $("raw-body").textContent = "加载中…";
    try {
      const r = await fetch("/api/raw");
      const j = await r.json();
      if (!j.available) { $("raw-body").textContent = j.message || "暂无原始响应。"; return; }
      $("raw-meta").textContent = `${j.file} · ${esc(j.fetched_at || "")} · 地区 ${esc(j.region || "-")}`;
      $("raw-body").textContent = j.preview + (j.truncated ? "\n\n… 已截断，完整内容见本地文件 data/raw/latest.json" : "");
    } catch (e) { $("raw-body").textContent = "读取失败：" + e.message; }
  });
  function closeModal() { $("raw-modal").hidden = true; }
  $("btn-raw-close").addEventListener("click", closeModal);
  $("raw-modal").addEventListener("click", (e) => { if (e.target === $("raw-modal")) closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeModal(); closeSettings(); }
  });

  load(state.days, false);
})();
