/* 周计划页：读 plan.json / coach.json / workout-checklist.md，不依赖后端。 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s === undefined || s === null ? "" : s)
    .replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function toast(msg) {
    const el = $("toast");
    el.textContent = msg; el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 3200);
  }

  async function main() {
    let plan, checklist;
    try {
      const r = await fetch("data/plan.json", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      plan = await r.json();
      const r2 = await fetch("data/workout-checklist.md", { cache: "no-store" });
      checklist = r2.ok ? await r2.text() : "清单暂不可用，请稍后刷新。";
    } catch (e) {
      $("coach-note").textContent = "周计划快照加载失败，请稍后刷新或检查 GitHub Actions 是否成功运行。";
      $("checklist").textContent = "清单暂不可用。";
      return;
    }
    const lvl = (plan.status || {}).level || "maintain";
    const badge = $("plan-status");
    badge.className = "plan-badge " + lvl;
    badge.textContent = "本周状态 · " + esc((plan.status || {}).label || "—");
    $("weekly-goal").textContent = plan.weekly_goal || "";
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
    $("checklist").textContent = checklist;
  }

  $("btn-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("checklist").textContent);
      toast("录入清单已复制");
    } catch (e) { toast("复制失败，请手动选中文本复制"); }
  });

  main();
})();
