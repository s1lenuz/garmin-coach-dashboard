/* 轻量 SVG 图表引擎：堆叠柱 + 折线 + 均线 + 参考带 + hover 提示 + 图例开关。
   零依赖、离线可用、跟随浅色主题。 */
(function (global) {
  "use strict";

  const DEFAULT_W = 920;
  const PAD = { top: 16, right: 16, bottom: 30, left: 52 };
  let uidCounter = 0;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function num(v, d) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    const decimals = d === undefined ? 1 : d;
    return Number(v).toFixed(decimals).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
  }
  function axis(s) { return s.axis || "left"; }
  function shortDate(s) { return typeof s === "string" && s.length >= 10 ? s.slice(5) : String(s); }

  function niceTicks(lo, hi, count) {
    if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const raw = (hi - lo) / (count || 4);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / mag;
    const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag;
    const l = Math.floor(lo / step) * step;
    const h = Math.ceil(hi / step) * step;
    const ticks = [];
    for (let v = l; v <= h + step * 0.0001; v += step) ticks.push(Number(v.toFixed(6)));
    return { ticks, lo: l, hi: h, step };
  }

  function domain(cfg, ax) {
    const opts = cfg[ax] || {};
    // 显式指定 min/max 时严格按该范围绘制（HRV 等需要截断 Y 轴才能看清波动）
    if (opts.min !== undefined && opts.max !== undefined && opts.max > opts.min) {
      let ticks;
      if (opts.step && opts.step > 0) {
        ticks = [];
        for (let v = opts.min; v <= opts.max + 1e-6; v += opts.step) ticks.push(Number(v.toFixed(6)));
      } else {
        const t = niceTicks(opts.min, opts.max, opts.ticks || 4);
        ticks = t.ticks.filter((v) => v >= opts.min - 1e-6 && v <= opts.max + 1e-6);
      }
      return { ticks, lo: opts.min, hi: opts.max, hasBars: false };
    }
    const n = cfg.labels.length;
    const vals = [];
    const stacks = {};
    let hasBars = false;
    cfg.series.forEach((s) => {
      if (axis(s) !== ax || s.hidden) return;
      if (s.type === "bar" && s.stack) {
        hasBars = true;
        if (!stacks[s.stack]) stacks[s.stack] = new Array(n).fill(0);
        s.data.forEach((v, i) => { if (v !== null && v !== undefined) stacks[s.stack][i] += Number(v); });
      } else {
        s.data.forEach((v) => { if (v !== null && v !== undefined && isFinite(v)) vals.push(Number(v)); });
      }
    });
    Object.keys(stacks).forEach((k) => stacks[k].forEach((v) => vals.push(v)));
    (cfg.bands || []).forEach((b) => { if ((b.axis || "left") === ax) { vals.push(b.from, b.to); } });
    (cfg.hlines || []).forEach((h) => { if ((h.axis || "left") === ax) vals.push(h.value); });
    if (opts.min !== undefined) vals.push(opts.min);
    if (opts.max !== undefined) vals.push(opts.max);
    if (!vals.length) return { ...niceTicks(0, 1, 4), hasBars };

    let lo = opts.min !== undefined ? opts.min : (hasBars ? 0 : Math.min.apply(null, vals));
    let hi = opts.max !== undefined ? opts.max : Math.max.apply(null, vals);
    if (opts.min === undefined && !hasBars) {
      const pad = (hi - lo) * 0.18 || 1;
      lo = Math.max(0, lo - pad);
      hi = hi + pad;
    } else if (opts.max === undefined && hasBars) {
      hi = hi * 1.08 || 1;
    }
    if (hi <= lo) hi = lo + 1;
    return { ...niceTicks(lo, hi, opts.ticks || 4), hasBars };
  }

  function lineSegments(data) {
    const segs = [];
    let cur = [];
    data.forEach((v, i) => {
      if (v === null || v === undefined || !isFinite(v)) {
        if (cur.length) { segs.push(cur); cur = []; }
      } else cur.push([i, Number(v)]);
    });
    if (cur.length) segs.push(cur);
    return segs;
  }

  function render(container, cfg) {
    if (!container) return;
    // viewBox 宽度跟随容器实际像素宽度，保证文字不被拉伸、刻度 1:1 清晰
    const W = Math.max(360, Math.round(container.getBoundingClientRect().width || DEFAULT_W));
    const uid = cfg.uid || "chart-" + (++uidCounter);
    const series = cfg.series || [];
    const labels = cfg.labels || [];
    const n = labels.length;
    const H = cfg.height || 260;
    const rightAxis = series.some((s) => axis(s) === "right");
    const padL = PAD.left;
    const padR = rightAxis ? 52 : PAD.right;
    const plotW = W - padL - padR;
    const plotH = H - PAD.top - PAD.bottom;
    if (!n || !plotW || !plotH) return;

    const left = domain(cfg, "left");
    const right = rightAxis ? domain(cfg, "right") : null;
    const bw = plotW / n;
    const barW = Math.max(1.2, Math.min(bw * 0.66, 26));
    const x = (i) => padL + i * bw + bw / 2;
    const y = (v, ax) => {
      const d = ax === "right" ? right : left;
      return PAD.top + (d.hi - v) / (d.hi - d.lo) * plotH;
    };
    const yLeft = (v) => y(v, "left");
    const yRight = (v) => y(v, "right");

    let svg = `<svg viewBox="0 0 ${W} ${H}" class="chart-svg" role="img" aria-label="${esc(cfg.title || "趋势图")}">`;
    svg += `<defs><clipPath id="clip-${uid}"><rect x="${padL}" y="${PAD.top}" width="${plotW}" height="${plotH}"/></clipPath></defs>`;

    // 水平网格 + 左轴刻度
    left.ticks.forEach((t) => {
      const yy = yLeft(t);
      if (yy < PAD.top - 0.5 || yy > PAD.top + plotH + 0.5) return;
      svg += `<line x1="${padL}" y1="${yy}" x2="${padL + plotW}" y2="${yy}" stroke="#e9edf5" stroke-width="1" ${t === 0 ? "" : 'stroke-dasharray="3 4"'} />`;
      const label = cfg.left && cfg.left.format ? cfg.left.format(t) : num(t, 0);
      svg += `<text x="${padL - 9}" y="${yy + 3.5}" text-anchor="end" class="ax-text">${esc(label)}</text>`;
    });
    if (rightAxis) {
      right.ticks.forEach((t) => {
        const yy = yRight(t);
        if (yy < PAD.top - 0.5 || yy > PAD.top + plotH + 0.5) return;
        const label = cfg.right && cfg.right.format ? cfg.right.format(t) : num(t, 0);
        svg += `<text x="${padL + plotW + 9}" y="${yy + 3.5}" text-anchor="start" class="ax-text">${esc(label)}</text>`;
      });
    }

    // 参考带（如 HRV 个人基线区间）
    (cfg.bands || []).forEach((b) => {
      const yy1 = y(b.to, b.axis || "left"), yy2 = y(b.from, b.axis || "left");
      const top = Math.min(yy1, yy2), h = Math.abs(yy2 - yy1);
      if (h <= 0) return;
      svg += `<rect x="${padL}" y="${top}" width="${plotW}" height="${h}" fill="${b.color}" opacity="0.09" />`;
      svg += `<line x1="${padL}" y1="${top}" x2="${padL + plotW}" y2="${top}" stroke="${b.color}" stroke-width="1" stroke-dasharray="4 4" opacity="0.55" />`;
      svg += `<line x1="${padL}" y1="${top + h}" x2="${padL + plotW}" y2="${top + h}" stroke="${b.color}" stroke-width="1" stroke-dasharray="4 4" opacity="0.55" />`;
      if (b.label) svg += `<text x="${padL + plotW - 6}" y="${top + 12}" text-anchor="end" class="band-text" fill="${b.color}">${esc(b.label)}</text>`;
    });

    // x 轴日期标签
    const step = Math.max(1, Math.ceil(n / (cfg.xLabelCount || 8)));
    for (let i = 0; i < n; i++) {
      if (i % step !== 0 && i !== n - 1) continue;
      svg += `<text x="${x(i)}" y="${H - 9}" text-anchor="middle" class="ax-text">${esc(shortDate(labels[i]))}</text>`;
    }

    svg += `<g clip-path="url(#clip-${uid})">`;
    // 堆叠柱
    const stackAcc = {};
    series.forEach((s) => {
      if (s.hidden || s.type !== "bar") return;
      const yy = (v) => y(v, axis(s));
      s.data.forEach((v, i) => {
        if (v === null || v === undefined || !isFinite(v)) return;
        const key = s.stack || `__single_${s.name}`;
        if (!stackAcc[key]) stackAcc[key] = new Array(n).fill(0);
        const base = stackAcc[key][i];
        const val = Number(v);
        stackAcc[key][i] += val;
        if (val <= 0) return;
        const top = yy(base + val), bottom = yy(base);
        const h = Math.max(0, bottom - top);
        const bx = x(i) - barW / 2;
        svg += `<rect x="${bx}" y="${top}" width="${barW}" height="${h}" fill="${s.color}" opacity="${s.opacity || 0.88}" rx="${Math.min(2, barW / 4)}"><title>${esc(s.name)} ${esc(shortDate(labels[i]))}: ${num(v, s.decimals === undefined ? 1 : s.decimals)}</title></rect>`;
      });
    });

    // 折线 / 面积
    series.forEach((s) => {
      if (s.hidden || s.type === "bar") return;
      const yy = (v) => y(v, axis(s));
      const segs = lineSegments(s.data);
      if (s.fill) {
        segs.forEach((seg) => {
          if (seg.length < 2) return;
          let d = `M ${x(seg[0][0])} ${PAD.top + plotH}`;
          seg.forEach((p) => { d += ` L ${x(p[0])} ${yy(p[1])}`; });
          d += ` L ${x(seg[seg.length - 1][0])} ${PAD.top + plotH} Z`;
          svg += `<path d="${d}" fill="${s.color}" opacity="${s.fillOpacity || 0.1}" />`;
        });
      }
      segs.forEach((seg) => {
        let d = "";
        seg.forEach((p, k) => { d += `${k ? " L " : "M "}${x(p[0])} ${yy(p[1])}`; });
        svg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" stroke-linejoin="round" stroke-linecap="round" ${s.dash ? 'stroke-dasharray="5 4"' : ""} opacity="${s.opacity || 1}" />`;
      });
      if (s.dots !== false) {
        segs.forEach((seg) => seg.forEach((p) => {
          if (n > 45 && seg.length > 45) return;
          svg += `<circle cx="${x(p[0])}" cy="${yy(p[1])}" r="${s.dotRadius || 2}" fill="#fff" stroke="${s.color}" stroke-width="1.5" />`;
        }));
      }
    });
    // 参考线
    (cfg.hlines || []).forEach((h) => {
      const yy = y(h.value, h.axis || "left");
      svg += `<line x1="${padL}" y1="${yy}" x2="${padL + plotW}" y2="${yy}" stroke="${h.color}" stroke-width="1.2" stroke-dasharray="6 4" opacity="0.8" />`;
      if (h.label) svg += `<text x="${padL + 6}" y="${yy - 5}" class="band-text" fill="${h.color}">${esc(h.label)}</text>`;
    });
    svg += `</g>`;
    svg += `<g class="hover-layer"></g>`;
    svg += `</svg>`;

    let legend = "";
    if (cfg.legend !== false) {
      legend = `<div class="chart-legend">` + series.map((s, idx) =>
        `<button type="button" class="legend-item ${s.hidden ? "is-off" : ""}" data-idx="${idx}" title="点击切换显示">
           <span class="legend-dot" style="background:${s.color}"></span>${esc(s.name)}</button>`
      ).join("") + `</div>`;
    }

    container.classList.add("chart-root");
    container.innerHTML = legend + `<div class="chart-canvas">${svg}<div class="chart-tooltip" hidden></div></div>`;
    const svgEl = container.querySelector("svg");
    const tip = container.querySelector(".chart-tooltip");
    const hoverLayer = container.querySelector(".hover-layer");

    container.querySelectorAll(".legend-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.idx);
        series[idx].hidden = !series[idx].hidden;
        render(container, cfg);
      });
    });

    function onMove(ev) {
      const rect = svgEl.getBoundingClientRect();
      const scale = W / rect.width;
      const localX = (ev.clientX - rect.left) * scale;
      let i = Math.floor((localX - padL) / bw);
      i = Math.max(0, Math.min(n - 1, i));
      const cx = x(i);
      let marks = `<line x1="${cx}" y1="${PAD.top}" x2="${cx}" y2="${PAD.top + plotH}" stroke="#94a3b8" stroke-width="1" stroke-dasharray="3 3" />`;
      series.forEach((s) => {
        if (s.hidden || s.type === "bar") return;
        const v = s.data[i];
        if (v === null || v === undefined || !isFinite(v)) return;
        marks += `<circle cx="${cx}" cy="${y(v, axis(s))}" r="4" fill="${s.color}" stroke="#fff" stroke-width="2" />`;
      });
      hoverLayer.innerHTML = marks;

      const rows = series.filter((s) => !s.hidden).map((s) => {
        const v = s.data[i];
        const fmt = s.format || ((z) => num(z, s.decimals === undefined ? 1 : s.decimals));
        return `<div class="tt-row"><span class="tt-dot" style="background:${s.color}"></span>
                <span class="tt-name">${esc(s.name)}</span>
                <span class="tt-val">${esc(fmt(v))}${esc(s.unit || "")}</span></div>`;
      }).join("");
      tip.innerHTML = `<div class="tt-date">${esc(labels[i])}</div>${rows}`;
      tip.hidden = false;
      const box = container.querySelector(".chart-canvas").getBoundingClientRect();
      const px = ev.clientX - box.left;
      const py = ev.clientY - box.top;
      const tw = tip.offsetWidth || 180, th = tip.offsetHeight || 100;
      tip.style.left = Math.max(4, Math.min(box.width - tw - 6, px + 14)) + "px";
      tip.style.top = Math.max(4, Math.min(box.height - th - 6, py - th - 12)) + "px";
    }
    function onLeave() { hoverLayer.innerHTML = ""; tip.hidden = true; }
    svgEl.addEventListener("mousemove", onMove);
    svgEl.addEventListener("mouseleave", onLeave);

    container.__chartCfg = cfg;
    if (!container.__chartResizeBound) {
      container.__chartResizeBound = true;
      let timer = null;
      window.addEventListener("resize", () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          if (document.contains(container) && container.__chartCfg) {
            render(container, container.__chartCfg);
          }
        }, 180);
      });
    }
  }

  function sparkline(container, data, color) {
    if (!container) return;
    const vals = (data || []).filter((v) => v !== null && v !== undefined && isFinite(v));
    if (vals.length < 2) { container.innerHTML = ""; return; }
    const w = 120, h = 34, p = 3;
    const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    const span = hi - lo || 1;
    const step = (w - p * 2) / (data.length - 1);
    let d = "", started = false;
    data.forEach((v, i) => {
      if (v === null || v === undefined || !isFinite(v)) { started = false; return; }
      const xx = p + i * step, yy = p + (hi - v) / span * (h - p * 2);
      d += `${started ? " L " : "M "}${xx.toFixed(1)} ${yy.toFixed(1)}`;
      started = true;
    });
    const last = vals[vals.length - 1];
    const ly = p + (hi - last) / span * (h - p * 2);
    container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="spark-svg">
      <path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${w - p}" cy="${ly.toFixed(1)}" r="2.6" fill="${color}" stroke="#fff" stroke-width="1.4"/></svg>`;
  }

  global.Chart = { render, sparkline, num };
})(window);
