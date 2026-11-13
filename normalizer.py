"""原始 JSON -> 统一按日记录。

原则：只做「已知字段名」的多候选映射；找不到就留 None。
不猜字段含义、不把缺失值当 0、不据此编造平均值或健康结论。
原始响应完整保存在 data/raw/ 供核对；若你的设备/地区返回了不同字段名，
只需在下面的 FIELD_CANDIDATES 里补一条候选路径即可。
"""
from __future__ import annotations

from datetime import date, timedelta

# 统一字段 -> 候选原始字段路径（按优先级）。路径用点号，遇到列表自动取首个 dict。
FIELD_CANDIDATES = {
    "sleep.date": ["calendarDate", "date", "day", "dailySleepDTO.calendarDate", "sleepStartTimestampGMT", "startTimeGMT"],
    # 实测 Personal API 返回 sleepScores.overall.value（0-100），不是 sleepScore.value
    "sleep.score": ["sleepScores.overall.value", "dailySleepDTO.sleepScores.overall.value",
                    "sleepScore.value", "dailySleepDTO.sleepScore.value",
                    "sleepScores.total.value", "sleepScore"],
    "sleep.duration_sec": ["sleepTimeSeconds", "dailySleepDTO.sleepTimeSeconds", "sleepDurationSeconds", "totalSleepSeconds", "sleepSeconds"],
    "sleep.deep_sec": ["deepSleepSeconds", "dailySleepDTO.deepSleepSeconds", "deepSleepDurationSeconds"],
    "sleep.light_sec": ["lightSleepSeconds", "dailySleepDTO.lightSleepSeconds", "lightSleepDurationSeconds"],
    "sleep.rem_sec": ["remSleepSeconds", "dailySleepDTO.remSleepSeconds", "remSleepDurationSeconds"],
    "sleep.awake_sec": ["awakeSleepSeconds", "dailySleepDTO.awakeSleepSeconds", "awakeDurationSeconds"],
    "sleep.spo2": ["averageSpO2Value", "averageSpo2Value", "dailySleepDTO.averageSpO2Value", "averageSpO2"],
    "sleep.respiration": ["averageRespirationValue", "dailySleepDTO.averageRespirationValue", "averageRespiration"],
    "sleep.resting_hr": ["restingHeartRate", "dailySleepDTO.restingHeartRate"],
    "sleep.avg_hrv": ["averageHRV", "averageHrv", "dailySleepDTO.averageHRV", "avgOvernightHrv"],
    "hrv.date": ["calendarDate", "date", "day", "hrvSummary.calendarDate", "measurementTimestampGMT"],
    "hrv.nightly_avg": ["lastNightAvg", "hrvSummary.lastNightAvg", "lastNightAverage", "value", "hrvValue"],
    "hrv.weekly_avg": ["weeklyAvg", "hrvSummary.weeklyAvg", "sevenDayAvg"],
    "hrv.night_high": ["lastNight5MinHigh", "hrvSummary.lastNight5MinHigh"],
    "hrv.baseline_low": ["baseline.balancedLow", "hrvSummary.baseline.balancedLow", "baseline.lowUpper", "baselineLow"],
    "hrv.baseline_high": ["baseline.balancedUpper", "hrvSummary.baseline.balancedUpper", "baselineUpper"],
    "hrv.status": ["status", "hrvSummary.status", "hrvStatus"],
    "summary.date": ["calendarDate", "date", "day", "startDate", "activityDate"],
    # 实测 /usersummary-service/v2/dailysummaries 的字段是嵌套结构
    "summary.total_kcal": ["calories.burnedTotal", "totalKilocalories", "totalCalories", "calories.total", "totalKcal"],
    "summary.active_kcal": ["calories.burnedActive", "activeKilocalories", "activeCalories", "calories.active", "activityKilocalories"],
    "summary.bmr_kcal": ["calories.burnedResting", "bmrKilocalories", "bmrCalories", "calories.bmr", "restingKilocalories"],
    "summary.steps": ["movement.steps.value", "totalSteps", "steps", "stepCount"],
    "summary.resting_hr": ["heartRate.restingValue", "restingHeartRate", "minHeartRate", "lowestHeartRate"],
    "summary.stress": ["stress.avgLevel", "averageStressLevel", "avgStressLevel", "averageStress"],
    "summary.body_battery": ["bodyBattery.latestValue", "bodyBatteryMostRecentValue", "bodyBatteryCurrentValue", "bodyBatteryHighestValue"],
    "summary.intensity_min": ["intensityMinutes.moderate", "moderateIntensityMinutes"],
}

CONTAINER_KEYS = (
    "dailySleepSummaries", "dailySleep", "hrvSummaries", "hrvSummaryList",
    "dailySummaries", "dailySummaryList", "userDailySummaries", "summaries",
    "data", "items", "results", "records", "_daily",
)


def _walk(obj, path: str):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            cur = next((x for x in cur if isinstance(x, dict)), None)
        if cur is None:
            return None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _pick(obj, key: str, numeric: bool = True):
    """numeric=True 时只接受数值，避免把 'GOOD' 之类的文本标签当成指标值。"""
    for path in FIELD_CANDIDATES.get(key, []):
        value = _walk(obj, path)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
        if not numeric and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _as_date(value) -> str | None:
    if isinstance(value, str) and value:
        text = value.strip()[:10]
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return text
    return None


def _date_of(obj: dict, kind: str) -> str | None:
    for path in FIELD_CANDIDATES.get(f"{kind}.date", []):
        got = _as_date(_walk(obj, path))
        if got:
            return got
    return None


def _iter_records(payload) -> list[tuple[str, dict]]:
    """兼容 list / {xxxList: []} / 单对象 / 逐日降级结构。"""
    if payload is None:
        return []
    items: list = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in CONTAINER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
            if isinstance(value, dict):
                items = [value]
                break
        else:
            items = [payload]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(item)
    return out


def empty_record(day: str) -> dict:
    return {
        "date": day,
        "sleep": {
            "score": None, "duration_min": None, "deep_min": None, "light_min": None,
            "rem_min": None, "awake_min": None, "spo2": None, "respiration": None,
            "resting_hr": None, "avg_hrv": None,
        },
        "hrv": {
            "nightly_avg": None, "weekly_avg": None, "night_high": None,
            "baseline_low": None, "baseline_high": None, "status": None,
        },
        "calories": {
            "total": None, "active": None, "bmr": None, "steps": None,
            "resting_hr": None, "stress": None, "body_battery": None, "intensity_min": None,
        },
    }


def _minutes(seconds):
    return round(seconds / 60.0, 1) if isinstance(seconds, (int, float)) and seconds > 0 else None


def _apply_sleep(rec: dict, obj: dict) -> None:
    s = rec["sleep"]
    s["score"] = _pick(obj, "sleep.score")
    s["duration_min"] = _minutes(_pick(obj, "sleep.duration_sec"))
    s["deep_min"] = _minutes(_pick(obj, "sleep.deep_sec"))
    s["light_min"] = _minutes(_pick(obj, "sleep.light_sec"))
    s["rem_min"] = _minutes(_pick(obj, "sleep.rem_sec"))
    s["awake_min"] = _minutes(_pick(obj, "sleep.awake_sec"))
    s["spo2"] = _pick(obj, "sleep.spo2")
    s["respiration"] = _pick(obj, "sleep.respiration")
    s["resting_hr"] = _pick(obj, "sleep.resting_hr")
    s["avg_hrv"] = _pick(obj, "sleep.avg_hrv")
    # 睡眠主体也可能整体嵌在 dailySleepDTO 下（FIELD_CANDIDATES 已含该前缀路径，此处再兜一层）
    nested = obj.get("dailySleepDTO") if isinstance(obj.get("dailySleepDTO"), dict) else None
    if nested:
        s["duration_min"] = s["duration_min"] or _minutes(_pick(nested, "sleep.duration_sec"))
        s["deep_min"] = s["deep_min"] or _minutes(_pick(nested, "sleep.deep_sec"))
        s["light_min"] = s["light_min"] or _minutes(_pick(nested, "sleep.light_sec"))
        s["rem_min"] = s["rem_min"] or _minutes(_pick(nested, "sleep.rem_sec"))
        s["awake_min"] = s["awake_min"] or _minutes(_pick(nested, "sleep.awake_sec"))
        s["score"] = s["score"] or _pick(nested, "sleep.score")


def _apply_hrv(rec: dict, obj: dict) -> None:
    h = rec["hrv"]
    h["nightly_avg"] = _pick(obj, "hrv.nightly_avg")
    h["weekly_avg"] = _pick(obj, "hrv.weekly_avg")
    h["night_high"] = _pick(obj, "hrv.night_high")
    h["baseline_low"] = _pick(obj, "hrv.baseline_low")
    h["baseline_high"] = _pick(obj, "hrv.baseline_high")
    h["status"] = _pick(obj, "hrv.status", numeric=False)
    if h["nightly_avg"] is None:
        nested = obj.get("hrvSummary") if isinstance(obj.get("hrvSummary"), dict) else None
        if nested:
            h["nightly_avg"] = _pick(nested, "hrv.nightly_avg")
            h["weekly_avg"] = h["weekly_avg"] or _pick(nested, "hrv.weekly_avg")
            h["baseline_low"] = h["baseline_low"] or _pick(nested, "hrv.baseline_low")
            h["baseline_high"] = h["baseline_high"] or _pick(nested, "hrv.baseline_high")
            h["status"] = h["status"] or _pick(nested, "hrv.status", numeric=False)


def _apply_summary(rec: dict, obj: dict) -> None:
    c = rec["calories"]
    c["total"] = _pick(obj, "summary.total_kcal")
    c["active"] = _pick(obj, "summary.active_kcal")
    c["bmr"] = _pick(obj, "summary.bmr_kcal")
    c["steps"] = _pick(obj, "summary.steps")
    c["resting_hr"] = _pick(obj, "summary.resting_hr")
    c["stress"] = _pick(obj, "summary.stress")
    c["body_battery"] = _pick(obj, "summary.body_battery")
    c["intensity_min"] = _pick(obj, "summary.intensity_min")
    if c["total"] is None and c["active"] is not None and c["bmr"] is not None:
        c["total"] = c["active"] + c["bmr"]  # 已知字段的确定换算，不是猜值


APPLIERS = {"sleep": _apply_sleep, "hrv": _apply_hrv, "summary": _apply_summary}


def normalize(fetch_result: dict) -> tuple[list[dict], dict]:
    start = date.fromisoformat(fetch_result["start"])
    days = int(fetch_result.get("days") or 30)
    table = {
        (start + timedelta(days=i)).isoformat(): empty_record((start + timedelta(days=i)).isoformat())
        for i in range(days)
    }
    payloads = fetch_result.get("payloads", {})
    per_kind: dict[str, list[dict]] = {}
    for kind, applier in APPLIERS.items():
        items = _iter_records(payloads.get(kind))
        per_kind[kind] = items
        for obj in items:
            day = _as_date(obj.get("_date")) or _date_of(obj, kind)
            if day in table:
                applier(table[day], obj)
    records = list(table.values())

    metrics = {
        "sleep_score": lambda r: r["sleep"]["score"],
        "sleep_duration": lambda r: r["sleep"]["duration_min"],
        "sleep_deep": lambda r: r["sleep"]["deep_min"],
        "sleep_rem": lambda r: r["sleep"]["rem_min"],
        "hrv": lambda r: r["hrv"]["nightly_avg"],
        "hrv_baseline_low": lambda r: r["hrv"]["baseline_low"],
        "hrv_baseline_high": lambda r: r["hrv"]["baseline_high"],
        "calories_total": lambda r: r["calories"]["total"],
        "calories_active": lambda r: r["calories"]["active"],
        "calories_bmr": lambda r: r["calories"]["bmr"],
        "steps": lambda r: r["calories"]["steps"],
    }
    coverage = {
        "days": days,
        "raw_counts": {k: len(v) for k, v in per_kind.items()},
        "metrics": {
            name: sum(1 for r in records if getter(r) is not None)
            for name, getter in metrics.items()
        },
    }
    return records, coverage
