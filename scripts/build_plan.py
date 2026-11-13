"""教练层：根据近期负荷 / 恢复状态生成未来 7 天训练课表、评语与日历。

对应架构图中的「算负荷 / 写评语 / 生成周计划」与反向链路
「我生成课表 → 佳明训练库 → Connect App → 推送到手表 → 你照着跑」。

重要事实：Garmin Connect **Personal API 是只读的**，没有写入训练计划的端点。
本模块因此产出「可导入 / 可订阅」的课表产物（plan.json / plan.ics / 录入清单），
并在 coach.html 给出手动导入步骤，绝不假装能自动推送到手表。

用法（一般由 build_site.py 调用）：
    from build_plan import build_plan
    plan = build_plan(records, analysis, interpretation, notes_text)
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 课表模板：按「状态档位」编排一周，强度从低到高
TEMPLATES = {
    "deload": [
        ("恢复日", "休息", 0, "完全休息或 20 分钟散步，睡前拉伸 10 分钟"),
        ("轻松活动", "低", 30, "快走 / 椭圆机，心率控制在最大心率 60% 以内"),
        ("技术练习", "低", 35, "球感 / 步频练习，不做冲刺"),
        ("恢复日", "休息", 0, "泡沫轴 + 髋踝活动度，早点睡"),
        ("轻松跑", "低", 35, "能完整说话的配速，结束后拉伸"),
        ("自主活动", "低", 40, "踢球 / 骑行任选，感到疲劳就提前收"),
        ("长睡眠日", "休息", 0, "不安排训练，把睡眠补到 8 小时"),
    ],
    "maintain": [
        ("轻松跑", "低", 40, "有氧基础，全程鼻呼吸"),
        ("间歇跑", "高", 45, "热身 10′ + 6×2′ 快跑（间休 2′）+ 放松 10′"),
        ("恢复日", "休息", 0, "拉伸 / 泡沫轴，关注 HRV 是否回到基线"),
        ("力量 + 核心", "中", 40, "深蹲、单腿硬拉、平板支撑各 3 组"),
        ("节奏跑", "中", 50, "20′ 节奏段，配速略吃力但可控"),
        ("长距离", "中", 70, "低强度有氧，按体感不按配速"),
        ("恢复日", "休息", 0, "完全休息，复盘本周数据"),
    ],
    "build": [
        ("轻松跑", "低", 45, "有氧打底，跑后做 4 组 strides"),
        ("间歇跑", "高", 50, "热身 10′ + 8×90″ 快跑（间休 90″）+ 放松 10′"),
        ("力量 + 核心", "中", 45, "下肢力量 4 组 + 核心 3 组"),
        ("恢复跑", "低", 35, "比轻松跑再慢一档，主动恢复"),
        ("节奏跑", "中", 55, "25′ 节奏段，收尾不冲刺"),
        ("长距离", "中", 85, "渐进长跑，最后 15′ 略提速"),
        ("恢复日", "休息", 0, "完全休息，把周计划完成情况记进 inbox/NOTES.md"),
    ],
}

STATUS_LABEL = {
    "deload": "减量恢复",
    "maintain": "稳态维持",
    "build": "稳步加量",
}


def _num(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _metric(analysis: dict, key: str, field: str = "avg7"):
    return (analysis.get("metrics") or {}).get(key, {}).get(field)


def _avg(values):
    vals = [v for v in values if v is not None and isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def assess(records: list[dict], analysis: dict) -> dict:
    """用近 14 天的客观数据判定恢复状态，规则完全透明、可复现。"""
    recent = records[-14:] if len(records) >= 14 else records
    hrv_vals = [r.get("hrv", {}).get("nightly_avg") for r in recent]
    lows = [r.get("hrv", {}).get("baseline_low") for r in recent]
    sleep_scores = [r.get("sleep", {}).get("score") for r in recent]
    active = [r.get("calories", {}).get("active") for r in recent]
    steps = [r.get("calories", {}).get("steps") for r in recent]

    hrv_avg = _avg(hrv_vals)
    baseline_low = _avg([v for v in lows if v is not None])
    below = sum(
        1 for r in recent
        if r.get("hrv", {}).get("nightly_avg") is not None
        and r.get("hrv", {}).get("baseline_low") is not None
        and r["hrv"]["nightly_avg"] < r["hrv"]["baseline_low"]
    )
    hrv_covered = sum(1 for v in hrv_vals if v is not None)
    below_ratio = below / hrv_covered if hrv_covered else 0.0
    sleep_avg = _avg(sleep_scores)
    active_avg = _avg(active)
    steps_avg = _avg(steps)

    # 判定：HRV 明显低于基线 / 睡眠评分偏低 -> 减量；反之且负荷稳定 -> 加量
    score = 0
    if baseline_low and hrv_avg:
        score += 2 if hrv_avg < baseline_low else -1
    if below_ratio >= 0.4:
        score += 2
    elif below_ratio <= 0.15 and hrv_covered >= 5:
        score -= 1
    if sleep_avg is not None:
        if sleep_avg < 70:
            score += 2
        elif sleep_avg >= 82:
            score -= 1
    if active_avg is not None and active_avg > 750:
        score += 1

    level = "deload" if score >= 3 else ("build" if score <= -2 else "maintain")
    reasons = []
    if hrv_avg is not None and baseline_low:
        reasons.append(f"近 14 天夜间 HRV 均值 {hrv_avg:.0f} ms，个人基线下限约 {baseline_low:.0f} ms")
    if hrv_covered:
        reasons.append(f"低于基线 {below}/{hrv_covered} 天")
    if sleep_avg is not None:
        reasons.append(f"睡眠评分均值 {sleep_avg:.0f} 分")
    if active_avg is not None:
        reasons.append(f"日均活动消耗 {active_avg:.0f} kcal")
    if steps_avg is not None:
        reasons.append(f"日均步数 {steps_avg:.0f}")

    return {
        "level": level,
        "label": STATUS_LABEL[level],
        "reason": "；".join(reasons) + "。" if reasons else "近期数据不足，按稳态维持编排。",
        "window_days": len(recent),
        "hrv_avg14": hrv_avg,
        "baseline_low": baseline_low,
        "below_baseline_days": below,
        "hrv_covered_days": hrv_covered,
        "sleep_score_avg14": sleep_avg,
        "active_kcal_avg14": active_avg,
        "steps_avg14": steps_avg,
    }


def _notes_tail(text: str, limit: int = 220) -> str:
    """取手写备注的最后几行（对应架构图里「你发」的踢球截图/文字）。

    NOTES.md 的用法说明放在 HTML 注释里，必须先整体剥离，否则模板文字会被当成留言。
    标题行、markdown 列表项与说明性符号开头的行同样忽略。
    """
    cleaned = re.sub(r"<!--[\s\S]*?-->", "", text or "")
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    body = [
        ln for ln in lines
        if not ln.startswith(("#", "-", "*", ">", "|", "<!--", "-->"))
        and len(ln) > 1
    ]
    tail = body[-3:]
    joined = " / ".join(tail)
    return joined[:limit]


def build_plan(
    records: list[dict],
    analysis: dict,
    interpretation: dict | None = None,
    notes_text: str = "",
    today: date | None = None,
) -> dict:
    """生成 7 天课表 + 教练评语 + 周计划文本。"""
    today = today or datetime.now(timezone.utc).astimezone().date()
    status = assess(records, analysis)
    interp = interpretation or {}
    overall = ((interp.get("sections") or {}).get("overall") or "").strip()
    highlights = interp.get("highlights") or []
    interp_source = interp.get("source", "rule")

    notes = _notes_tail(notes_text)
    coach_parts = []
    coach_parts.append(
        f"本周判定：**{status['label']}**。{status['reason']}"
    )
    if overall:
        # 只摘取前两句结论，避免把「不构成医疗建议」等免责声明塞进教练口吻里
        sentences = [s.strip() for s in overall.replace("\n", " ").split("。") if s.strip()]
        keep = [s for s in sentences if "医疗建议" not in s and "专业人士" not in s][:2]
        snippet = "。".join(keep)
        if snippet:
            coach_parts.append("AI 趋势解读：" + snippet[:180] + "。")
    if highlights:
        coach_parts.append("关键提示：" + "、".join(str(x) for x in highlights[:3]) + "。")
    if notes:
        coach_parts.append("你的留言：「" + notes + "」——记下了，按这个状态微调本周强度。")
    coach_parts.append("课表是建议不是医嘱；HRV 连续两天低于基线就把当天改成休息。")
    coach_note = "\n".join(coach_parts)

    template = TEMPLATES[status["level"]]
    days = []
    for i, (name, intensity, minutes, detail) in enumerate(template):
        d = today + timedelta(days=i)
        days.append({
            "date": d.isoformat(),
            "weekday": WEEKDAYS[d.weekday()],
            "type": name,
            "intensity": intensity,
            "duration_min": minutes,
            "detail": detail,
            "calendar_time": "06:47" if minutes else "08:00",
            "rest": minutes == 0,
        })

    total_min = sum(x["duration_min"] for x in days)
    weekly_goal = (
        f"本周合计建议训练 {len([x for x in days if not x['rest']])} 次 / {total_min} 分钟，"
        f"以「{status['label']}」为主轴；重点观察 HRV 是否回到个人基线区间。"
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "week_start": days[0]["date"],
        "week_end": days[-1]["date"],
        "status": status,
        "coach_note": coach_note,
        "coach_note_source": interp_source,
        "weekly_goal": weekly_goal,
        "days": days,
        "user_note": notes,
        "readonly_note": (
            "Garmin Connect Personal API 目前只提供只读端点，无法把训练课表写回 Connect 训练库或推送到手表。"
            "请通过下方 ICS 日历订阅或在 Connect 网页版手动创建训练；本页提供可直接复制的录入清单。"
        ),
        "import_steps": [
            "订阅 plan.ics（手机日历 / Outlook / Google Calendar 均可自动同步，按课表时间提前 10 分钟提醒）",
            "或在 Garmin Connect 网页版 → 训练 → 训练计划 / 日历中按下方清单手动创建，保存后同步到手表",
            "第三方训练平台（TrainingPeaks 等）如有官方写入权限，可自行对接 plan.json",
            "Personal API Key 到期后到 Connect 设置页重建，并在 GitHub Secrets 中更新",
        ],
    }


def to_ics(plan: dict, calendar_name: str = "Garmin 教练周计划") -> str:
    """生成 RFC 5545 日历；北京时间 06:47 换算为 UTC 写入，避免 VTIMEZONE 依赖。"""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//garmin-coach-dashboard//weekly-plan//ZH",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{calendar_name}",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]
    for item in plan.get("days", []):
        try:
            d = date.fromisoformat(item["date"])
        except (KeyError, ValueError):
            continue
        hh, mm = (item.get("calendar_time") or "06:47").split(":")
        # 北京时间 = UTC+8；当天 06:47 -> 前一天 22:47 UTC
        local = datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=timezone(timedelta(hours=8)))
        start = local.astimezone(timezone.utc)
        end = start + timedelta(minutes=max(30, int(item.get("duration_min") or 30)))
        uid = f"garmin-coach-{item['date']}@pages.local"
        title = f"{item['type']} · {item['duration_min']} 分钟" if item.get("duration_min") else f"{item['type']} · 恢复"
        desc = (item.get("detail") or "").replace("\n", " ").replace(",", "\\,").replace(";", "\\;")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{title}",
            f"DESCRIPTION:{desc}",
            "BEGIN:VALARM",
            "TRIGGER:-PT10M",
            "ACTION:DISPLAY",
            "DESCRIPTION:训练提醒",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def to_checklist(plan: dict) -> str:
    """可直接复制进 Garmin Connect「创建训练」的文本清单。"""
    out = [f"# {plan['week_start']} ~ {plan['week_end']} 训练课表（{plan['status']['label']}）", ""]
    for item in plan["days"]:
        if item["rest"]:
            out.append(f"- {item['date']} {item['weekday']}：{item['type']}（不安排训练）")
        else:
            out.append(
                f"- {item['date']} {item['weekday']}：{item['type']} / "
                f"{item['duration_min']} 分钟 / 强度 {item['intensity']} / {item['detail']}"
            )
    out += ["", plan["weekly_goal"], "", plan["readonly_note"]]
    return "\n".join(out)


def write_outputs(plan: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "plan.ics").write_text(to_ics(plan), encoding="utf-8")
    (out_dir / "coach.json").write_text(
        json.dumps({
            "generated_at": plan["generated_at"],
            "status": plan["status"],
            "coach_note": plan["coach_note"],
            "weekly_goal": plan["weekly_goal"],
            "user_note": plan["user_note"],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "workout-checklist.md").write_text(to_checklist(plan), encoding="utf-8")
    return {"files": ["plan.json", "plan.ics", "coach.json", "workout-checklist.md"]}


if __name__ == "__main__":
    # 独立自测：用演示数据跑一遍，不触碰真实账号
    import sys
    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))
    import analyzer
    import mock_data

    recs = mock_data.generate(90)
    a = analyzer.analyze(recs, 30)
    p = build_plan(recs, a, notes_text="# 我的随手记\n昨天踢球进了个神仙球，右腿有点酸\n")
    print(json.dumps({
        "status": p["status"]["label"],
        "days": len(p["days"]),
        "weekly_goal": p["weekly_goal"],
        "note_has_user_text": "神仙球" in p["coach_note"],
        "ics_lines": len(to_ics(p).splitlines()),
    }, ensure_ascii=False, indent=2))
