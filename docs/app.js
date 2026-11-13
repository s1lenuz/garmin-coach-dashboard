/* GitHub Pages 静态大屏：读取 Actions 每日生成的 JSON 快照，不依赖任何后端。
   数据源优先级 snapshot-{days}.json → demo-{days}.json（内置演示兜底）。
   代码里没有任何 API Key；所有凭证只存在于 GitHub Actions Secrets。 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s === undefined || s === null ? "" : s)
    .replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d) => (v === null || v === undefined || !isFinite(v) ? "—" : Chart.num(v, d === undefined ? 1 : d));
  const text2html = (s) => esc(s).replace(/\n/g, "<br>");

  const state = { days: 30, data: null, plan: null, meta: null, busy: false };
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

  async function getJSON(path, bust) {
    const url = path + (bust ? "?v=" + Date.now() : "");
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  async function load(days, bust) {
    if (state.busy) return;
    state.busy = true;
    state.days = days || state.days;
    $("btn-refresh").disabled = true;
    $("source-badge").className = "badge badge-loading";
    $("source-badge").textContent = "加载中…";
    let fallback = false;
    try {
      state.data = await getJSON("data/snapshot-" + state.days + ".json", bust);
    } catch (e) {
      try {
        state.data = await getJSON("data/demo-" + state.days + ".json", false);
        fallback = true;
      } catch (e2) {
        $("notice").hidden = false;
        $("notice").className = "notice error";
        $("notice").innerHTML = "<b>快照加载失败</b>：请稍后重新载入，或检查 GitHub Actions 的 daily workflow 是否成功运行。";
        state.busy = false;
        $("btn-refresh").disabled = false;
        return;
      }
    }
    state.busy = false;
    $("btn-refresh").disabled = false;
    document.querySelectorAll("#range-tabs button").forEach((b) => {
      b.classList.toggle("active", Number(b.dataset.days) === state.days);
    });
    render(fallback);
    if (bust) toast("快照已重新载入");
  }

  function render(fallback) {
    if (!state.data) return;
    renderHeader(fallback);
    renderKPI();
    renderCharts();
    renderAI();
    renderSide();
  }

  function renderHeader(fallback) {
    const meta = state.data.meta || {};
    const badge = $("source-badge");
    const isReal = meta.source === "garmin";
    badge.className = "badge " + (isReal ? "badge-live" : "badge-demo");
    badge.textContent = isReal ? "Garmin 快照" : "演示数据";

    const notes = [];
    if (fallback) notes.push("真实快照暂不可用，当前展示的是<b>内置演示数据</b>。");
    if (meta.fallback_reason) notes.push(esc(meta.fallback_reason));
    if (meta.errors && Object.keys(meta.errors).length) {
      notes.push("本次构建有 " + Object.keys(meta.errors).length + " 项端点告警，详情见构建元信息。");
    }
    if (meta.fallbacks && meta.fallbacks.length) notes.push("构建时已降级为逐日拉取端点：" + esc(meta.fallbacks.join("、")));
    if (!isReal) notes.push("未检测到真实数据：请在仓库 Settings → Secrets and variables → Actions 中配置 <b>GARMIN_PERSONAL_API_KEY</b> 并手动运行一次 daily workflow。");

    const box = $("notice");
    if (notes.length) {
      box.hidden = false;
      box.className = "notice " + (meta.errors && Object.keys(meta.errors).length ? "error" : "info");
      box.innerHTML = notes.map((n) => "<div>" + n + "</div>").join("");
    } else {
      box.hidden = true;
    }

    const built = meta.generated_at || meta.fetched_at;
    $("sb-source").innerHTML = "数据源：<b>" + (isReal ? "Garmin Personal API" : "内置演示数据") + "</b>";
    $("sb-built").textContent = "快照时间 " + (built ? new Date(built).toLocaleString("zh-CN") : "—");
    $("sb-dot").className = "sb-dot" + (isReal ? "" : " mock");
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

    const h = m.hrv || {};
    const low = recs.find((r) => r.hrv.baseline_low !== null)?.hrv.baseline_low;
    const high = recs.find((r) => r.hrv.baseline_high !== null)?.hrv.baseline_high;
    const bands = (low !== null && low !== undefined && high !== null && high !== undefined)
      ? [{ from: low, to: high, color: C.hrv, label: "个人基线区间" }] : [];
    const belowCount = recs.filter((r) => r.hrv.nightly_avg !== null && low !== null && low !== undefined && r.hrv.nightly_avg < low).length;
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
    $("ai-source-tag").textContent = interp.source === "deepseek" || interp.source === "cache" ? "DEEPSEEK" : "RULES";
    $("ai-foot").innerHTML =
      `<span>解读来源：<b>${sourceText}</b></span><span>生成时间：${esc(t)}</span>` +
      (interp.warning ? `<span class="warn-text">${esc(interp.warning)}</span>` : "") +
      `<span>注：只向模型发送聚合统计，不发送逐日健康明细或任何凭证。</span>`;
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
    }).join("") + `<p class="fine">数据源：<b>${esc(meta.source === "garmin" ? "Garmin Personal API 快照" : "内置演示数据")}</b>` +
      (meta.region ? ` · 地区主机 ${esc(meta.region)}` : "") +
      (meta.fetched_at ? `<br>抓取时间 ${esc(new Date(meta.fetched_at).toLocaleString("zh-CN"))}` : "") + `</p>`;
  }

  // ---------------- 周计划 ----------------
  async function loadPlan() {
    try {
      const plan = await getJSON("data/plan.json", false);
      state.plan = plan;
      renderPlan(plan);
    } catch (e) {
      $("coach-note").textContent = "周计划快照暂不可用，请稍后重新载入。";
      $("plan-grid").innerHTML = "";
    }
  }

  function renderPlan(plan) {
    const lvl = (plan.status || {}).level || "maintain";
    const badge = $("plan-status");
    badge.className = "plan-badge " + lvl;
    badge.textContent = "本周状态 · " + esc((plan.status || {}).label || "—");
    $("weekly-goal").textContent = plan.weekly_goal || "按近期负荷与恢复状态自动编排";
    $("coach-note").textContent = plan.coach_note || "";
    const today = new Date().toISOString().slice(0, 10);
    $("plan-grid").innerHTML = (plan.days || []).map((d) => `
      <div class="plan-day ${d.date === today ? "today" : ""} ${d.rest ? "rest" : ""}">
        <span class="pd-date">${esc(d.date.slice(5))} ${esc(d.weekday)}</span>
        <span class="pd-type">${esc(d.type)}</span>
        <span class="pd-tag ${esc(d.intensity)}">${d.rest ? "休息" : esc(d.intensity) + "强度 · " + d.duration_min + "′"}</span>
        <span class="pd-detail">${esc(d.detail)}</span>
      </div>`).join("");
    $("readonly-box").innerHTML =
      `<b>${esc(plan.readonly_note || "")}</b>` +
      `<ol>${(plan.import_steps || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`;
  }

  async function loadMeta() {
    try {
      const m = await getJSON("data/meta.json", false);
      state.meta = m;
      if (m.built_at_local) $("sb-built").textContent = "构建时间 " + esc(m.built_at_local) + "（北京时间）";
      if (m.schedule) $("sb-schedule").textContent = esc(m.schedule.split("，")[0]);
    } catch (e) { /* 元信息缺失不影响主看板 */ }
  }

  // ---------------- 交互 ----------------
  $("range-tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-days]");
    if (btn) load(Number(btn.dataset.days), false);
  });
  $("btn-refresh").addEventListener("click", () => {
    load(state.days, true);
    loadPlan();
    loadMeta();
    toast("正在重新载入最新快照…");
  });
  $("btn-copy-plan").addEventListener("click", async () => {
    try {
      const r = await fetch("data/workout-checklist.md", { cache: "no-store" });
      const text = await r.text();
      await navigator.clipboard.writeText(text);
      toast("训练录入清单已复制");
    } catch (e) { toast("复制失败，请从「录入清单」页面手动复制"); }
  });

  load(state.days, false);
  loadPlan();
  loadMeta();
})();
