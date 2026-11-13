"""统计层：趋势、环比、滚动均值、异常日、相关性，以及给大模型的结构化摘要。

所有统计都跳过缺失值（None），不会把缺失当 0，也不会外推。
"""
from __future__ import annotations

import math

METRICS = {
    "sleep_score": {"label": "睡眠评分", "unit": "分", "decimals": 0, "better": "up",
                    "get": lambda r: r["sleep"]["score"]},
    "sleep_duration": {"label": "睡眠时长", "unit": "小时", "decimals": 1, "better": "up",
                       "get": lambda r: (r["sleep"]["duration_min"] / 60.0) if r["sleep"]["duration_min"] is not None else None},
    "deep_ratio": {"label": "深睡占比", "unit": "%", "decimals": 1, "better": "up",
                   "get": lambda r: (r["sleep"]["deep_min"] / r["sleep"]["duration_min"] * 100.0)
                   if r["sleep"]["deep_min"] is not None and r["sleep"]["duration_min"] else None},
    "hrv": {"label": "夜间 HRV", "unit": "ms", "decimals": 1, "better": "up",
            "get": lambda r: r["hrv"]["nightly_avg"]},
    "calories_total": {"label": "总消耗", "unit": "kcal", "decimals": 0, "better": "neutral",
                       "get": lambda r: r["calories"]["total"]},
    "calories_active": {"label": "活动消耗", "unit": "kcal", "decimals": 0, "better": "neutral",
                        "get": lambda r: r["calories"]["active"]},
    "steps": {"label": "步数", "unit": "步", "decimals": 0, "better": "neutral",
              "get": lambda r: r["calories"]["steps"]},
    "stress": {"label": "平均压力", "unit": "", "decimals": 0, "better": "down",
               "get": lambda r: r["calories"]["stress"]},
}


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _mean(values):
    return sum(values) / len(values) if values else None


def _std(values):
    return (sum((v - _mean(values)) ** 2 for v in values) / len(values)) ** 0.5 if len(values) > 1 else 0.0


def _slope(pairs):
    """最小二乘斜率，单位：指标/天。"""
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    den = sum((p[0] - mx) ** 2 for p in pairs)
    return sum((p[0] - mx) * (p[1] - my) for p in pairs) / den if den else None


def _moving_average(records, getter, window=7):
    out = []
    for i in range(len(records)):
        chunk = [
            _num(getter(r))
            for r in records[max(0, i - window + 1) : i + 1]
        ]
        chunk = [v for v in chunk if v is not None]
        out.append(round(sum(chunk) / len(chunk), 2) if len(chunk) >= max(2, window // 2) else None)
    return out


def _metric(records, key, spec):
    getter = spec["get"]
    series = [{"date": r["date"], "value": _num(getter(r))} for r in records]
    values = [(i, x["value"]) for i, x in enumerate(series) if x["value"] is not None]
    nums = [v for _, v in values]
    last = next((x for x in reversed(series) if x["value"] is not None), None)
    recent = [x["value"] for x in series[-7:] if x["value"] is not None]
    previous = [x["value"] for x in series[-14:-7] if x["value"] is not None]
    avg7, prev7 = _mean(recent), _mean(previous)
    change = ((avg7 - prev7) / prev7 * 100.0) if (avg7 is not None and prev7) else None
    slope = _slope(values)
    mu, sd = _mean(nums), _std(nums)
    anomalies = []
    if mu is not None and sd > 0:
        for i, v in values:
            z = (v - mu) / sd
            if abs(z) >= 1.6:
                anomalies.append({"date": records[i]["date"], "value": round(v, 1),
                                  "direction": "偏高" if z > 0 else "偏低", "z": round(z, 1)})
    trend = None
    if slope is not None and mu:
        magnitude = slope * min(len(records), 30) / abs(mu) * 100.0
        trend = "上升" if magnitude > 4 else ("下降" if magnitude < -4 else "平稳")
    return {
        "key": key, "label": spec["label"], "unit": spec["unit"],
        "decimals": spec["decimals"], "better": spec["better"],
        "series": series,
        "ma7": _moving_average(records, getter),
        "latest": last["value"] if last else None,
        "latest_date": last["date"] if last else None,
        "avg7": avg7, "prev7": prev7, "change_pct": change,
        "slope_per_day": slope, "trend": trend,
        "min": min(nums) if nums else None, "max": max(nums) if nums else None,
        "mean": mu, "std": sd, "covered": len(nums),
        "anomalies": anomalies,
    }


def _pearson(pairs):
    if len(pairs) < 6:
        return None
    xs, ys = zip(*pairs)
    mx, my = _mean(list(xs)), _mean(list(ys))
    den_x = sum((x - mx) ** 2 for x in xs)
    den_y = sum((y - my) ** 2 for y in ys)
    if not den_x or not den_y:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(den_x * den_y)


def _lagged_pairs(records, x_getter, y_getter, lag=1):
    out = []
    for i in range(len(records) - lag):
        x, y = _num(x_getter(records[i])), _num(y_getter(records[i + lag]))
        if x is not None and y is not None:
            out.append((x, y))
    return out


def _correlation_rows(records):
    specs = [
        ("睡眠时长 → 次日 HRV", METRICS["sleep_duration"]["get"], METRICS["hrv"]["get"], 1),
        ("睡眠评分 → 次日 HRV", METRICS["sleep_score"]["get"], METRICS["hrv"]["get"], 1),
        ("HRV → 当日睡眠评分", METRICS["hrv"]["get"], METRICS["sleep_score"]["get"], 0),
        ("活动消耗 → 次日睡眠时长", METRICS["calories_active"]["get"], METRICS["sleep_duration"]["get"], 1),
    ]
    rows = []
    for label, xg, yg, lag in specs:
        pairs = _lagged_pairs(records, xg, yg, lag)
        r = _pearson(pairs)
        if r is None:
            continue
        strength = "强" if abs(r) >= 0.6 else ("中等" if abs(r) >= 0.3 else "弱")
        rows.append({"label": label, "r": round(r, 2), "n": len(pairs),
                     "strength": strength, "direction": "正相关" if r > 0 else "负相关"})
    return rows


def _weekly(records):
    from datetime import date
    buckets: dict[str, list[dict]] = {}
    for rec in records:
        d = date.fromisoformat(rec["date"])
        monday = date.fromordinal(d.toordinal() - d.weekday()).isoformat()
        buckets.setdefault(monday, []).append(rec)
    rows = []
    for week in sorted(buckets):
        chunk = buckets[week]
        row = {"week": week, "days": len(chunk)}
        for key in ("sleep_score", "sleep_duration", "deep_ratio", "hrv",
                    "calories_total", "calories_active", "steps"):
            values = [_num(METRICS[key]["get"](r)) for r in chunk]
            values = [v for v in values if v is not None]
            row[key] = round(_mean(values), 1) if values else None
        rows.append(row)
    return rows


def _fmt(value, decimals=1, unit=""):
    return f"{value:.{decimals}f}{unit}" if isinstance(value, (int, float)) else "暂无数据"


def _dir_text(change, unit="%"):
    if change is None:
        return "暂无足够数据做环比"
    word = "上升" if change > 0 else ("下降" if change < 0 else "持平")
    return f"较前 7 日{word} {abs(change):.1f}{unit}"


def rule_sections(analysis: dict) -> dict:
    """纯统计驱动的解读，用于无大模型 / 模型失败 / 离线演示时降级。"""
    m = analysis["metrics"]
    s, d, h = m["sleep_score"], m["sleep_duration"], m["hrv"]
    c, a = m["calories_total"], m["calories_active"]
    deep = m["deep_ratio"]
    corr = analysis["correlations"]
    low_days = sum(
        1 for x in d["series"][-30:] if x["value"] is not None and x["value"] < 7
    )
    baseline_low = next(
        (r["hrv"]["baseline_low"] for r in analysis["records"] if r["hrv"]["baseline_low"] is not None), None
    )
    baseline_high = next(
        (r["hrv"]["baseline_high"] for r in analysis["records"] if r["hrv"]["baseline_high"] is not None), None
    )
    below = sum(
        1 for x in h["series"][-30:]
        if x["value"] is not None and baseline_low is not None and x["value"] < baseline_low
    )

    sleep_text = (
        f"近 7 日平均睡眠 {_fmt(d['avg7'], 1, ' 小时')}，睡眠评分 {_fmt(s['avg7'], 0, ' 分')}，"
        f"{_dir_text(d['change_pct'])}。深睡占比均值 {_fmt(deep['avg7'], 1, '%')}。"
        f"最近 30 天中有 {low_days} 天睡眠不足 7 小时"
        + (f"；另有 {len(d['series']) - d['covered']} 天无有效记录" if d["covered"] < len(d["series"]) else "")
        + "。"
    )
    hrv_text = (
        f"近 7 日夜间 HRV 均值 {_fmt(h['avg7'], 1, ' ms')}，{_dir_text(h['change_pct'])}；"
        f"整体趋势{h['trend'] or '不明确'}，区间 {_fmt(h['min'], 0)}–{_fmt(h['max'], 0)} ms。"
        + (f"最近 30 天有 {below} 天低于个人基线区间（{_fmt(baseline_low, 0)}–{_fmt(baseline_high, 0)} ms）。"
           if baseline_low is not None else "该数据源未提供 HRV 个人基线区间。")
    )
    cal_text = (
        f"日均总消耗 {_fmt(c['avg7'], 0, ' kcal')}，其中活动消耗 {_fmt(a['avg7'], 0, ' kcal')}，"
        f"{_dir_text(a['change_pct'])}。总消耗趋势{c['trend'] or '不明确'}。"
        f"活动消耗占均值比约 "
        + (_fmt(a["avg7"] / c["avg7"] * 100, 0, "%") if a["avg7"] and c["avg7"] else "暂无数据") + "。"
    )
    if corr:
        strongest = max(corr, key=lambda x: abs(x["r"]))
        link = f"相关性上，{strongest['label']} 呈{strongest['strength']}{strongest['direction']}（r={strongest['r']}，基于 {strongest['n']} 组配对）。"
    else:
        link = "有效配对样本不足，暂不计算指标间相关性。"
    overall = (
        link + "以上均为描述性统计，不构成医疗建议；若出现连续多日的指标同步恶化，"
        "建议结合主观疲劳感、训练安排与作息一起判断，必要时咨询专业人士。"
    )
    return {"sleep": sleep_text, "hrv": hrv_text, "calories": cal_text, "overall": overall}


def analyze(records: list[dict], days: int) -> dict:
    metrics = {key: _metric(records, key, spec) for key, spec in METRICS.items()}
    anomalies = []
    for key in ("sleep_score", "sleep_duration", "hrv", "calories_active"):
        for item in metrics[key]["anomalies"][-3:]:
            anomalies.append({**item, "metric": metrics[key]["label"],
                              "unit": metrics[key]["unit"]})
    anomalies.sort(key=lambda x: x["date"], reverse=True)
    analysis = {
        "records": records,
        "days": days,
        "start": records[0]["date"] if records else None,
        "end": records[-1]["date"] if records else None,
        "metrics": metrics,
        "correlations": _correlation_rows(records),
        "anomalies": anomalies[:10],
        "weekly": _weekly(records),
        "missing_days": sum(
            1 for r in records
            if all(r["sleep"][k] is None for k in ("score", "duration_min"))
            and r["hrv"]["nightly_avg"] is None
        ),
    }
    analysis["rule_sections"] = rule_sections(analysis)
    compact = {
        "range": {"start": analysis["start"], "end": analysis["end"], "days": days},
        "data_quality": {"missing_days": analysis["missing_days"]},
        "metrics": {
            k: {
                "label": v["label"], "unit": v["unit"],
                "last_7d_mean": round(v["avg7"], 2) if v["avg7"] is not None else None,
                "previous_7d_mean": round(v["prev7"], 2) if v["prev7"] is not None else None,
                "change_pct": round(v["change_pct"], 1) if v["change_pct"] is not None else None,
                "trend": v["trend"], "min": v["min"], "max": v["max"],
                "covered_days": v["covered"],
            }
            for k, v in metrics.items()
        },
        "weekly_means": analysis["weekly"][-12:],
        "correlations": analysis["correlations"],
        "notable_days": analysis["anomalies"][:6],
    }
    analysis["llm_payload"] = compact
    return analysis
