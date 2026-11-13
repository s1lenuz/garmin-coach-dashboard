/* 轻量 Widget：只读 data/widget.json，零依赖，适合 iframe / 手机小组件。 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s === undefined || s === null ? "" : s)
    .replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function spark(values, color) {
    const vals = (values || []).filter((v) => typeof v === "number" && isFinite(v));
    if (vals.length < 2) return "";
    const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    const span = hi - lo || 1;
    const pts = vals.map((v, i) => {
      const x = (i / (vals.length - 1)) * 100;
      const y = 24 - ((v - lo) / span) * 20;
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    return `<svg viewBox="0 0 100 26" preserveAspectRatio="none" aria-hidden="true">
      <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"
        stroke-linecap="round" stroke-linejoin="round" opacity=".85"/></svg>`;
  }

  async function main() {
    let d;
    try {
      const r = await fetch("data/widget.json", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      d = await r.json();
    } catch (e) {
      $("widget-sub").textContent = "快照加载失败，请稍后重试";
      return;
    }
    const colors = { sleep_score: "#6d5ce7", sleep_duration: "#8b83f8", hrv: "#0d9488", calories_total: "#f59e0b" };
    $("widget-kpis").innerHTML = (d.kpis || []).map((k) => {
      const v = k.value === null || k.value === undefined ? "—" : Number(k.value).toFixed(k.digits || 0);
      const ch = k.change_pct;
      const arrow = ch === null || ch === undefined ? "" : (ch > 0 ? "↑" : ch < 0 ? "↓" : "→") + " " + Math.abs(ch).toFixed(1) + "%";
      return `<div class="widget-kpi">
        <div class="wk-label">${esc(k.label)}</div>
        <div class="wk-value">${esc(v)}<small>${esc(k.unit)}</small></div>
        ${spark((d.spark || {})[k.key], colors[k.key] || "#6d5ce7")}
        <div class="wk-label">${esc(arrow || "近 7 日均值")}</div>
      </div>`;
    }).join("");

    const p = d.today_plan || {};
    $("widget-plan-main").textContent = p.duration_min
      ? `${p.type} · ${p.duration_min} 分钟` : `${p.type || "—"} · 恢复`;
    $("widget-plan-detail").textContent = p.detail || "";
    $("widget-sub").textContent = `本周状态：${d.status || "—"} · 每日 06:47 自动更新`;
    $("widget-updated").textContent = d.updated_at
      ? "更新 " + new Date(d.updated_at).toLocaleString("zh-CN") : "—";

    if (new URLSearchParams(location.search).get("compact") === "1") {
      $("widget-tips").style.display = "none";
    }
  }
  main();
})();
