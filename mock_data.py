"""可复现的 90 天演示数据（无需任何凭证即可完整体验看板）。

刻意植入了真实数据里常见的现象：周周期、缓慢趋势、连续几天的状态下滑、
偶尔熬夜、个别日期未佩戴导致的数据缺失——方便检验图表与 AI 解读的表现。
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta


def generate(days: int = 90, seed: int = 20261112) -> list[dict]:
    rnd = random.Random(seed)
    end = date.today()
    records: list[dict] = []
    prev_sleep: float | None = None

    for i in range(days):
        day = end - timedelta(days=days - 1 - i)
        weekend = day.weekday() >= 5
        trend = 3.0 * math.sin(i / 19.0) + (i / max(days, 1)) * 2.4
        sick = 58 <= i <= 61            # 连续 4 天身体状态下滑
        late_night = rnd.random() < 0.08

        # ---- 缺失：约 4% 的日期没戴表 ----
        missing = rnd.random() < 0.04

        sleep_min = 405 + (42 if weekend else 0) + trend * 1.8 + rnd.gauss(0, 26)
        if late_night:
            sleep_min -= rnd.uniform(65, 115)
        sleep_min = max(245, min(590, sleep_min))
        if not missing:
            deep = sleep_min * rnd.uniform(0.14, 0.22)
            rem = sleep_min * rnd.uniform(0.17, 0.25)
            awake = rnd.uniform(16, 46)
            light = max(0.0, sleep_min - deep - rem - awake)
            score = max(
                48,
                min(
                    96,
                    58 + (sleep_min - 360) / 9.0 + (deep / sleep_min - 0.16) * 90 + rnd.gauss(0, 5),
                ),
            )
        else:
            deep = rem = awake = light = 0.0
            score = 0.0

        # ---- HRV：与前一天睡眠正相关，并受身体状态影响 ----
        hrv = 45.5 + trend
        if prev_sleep is not None:
            hrv += (prev_sleep - 420) / 30.0 * 2.6
        hrv += rnd.gauss(0, 4.2)
        if sick:
            hrv -= 13.0
        hrv = max(22.0, min(78.0, hrv))

        # ---- 卡路里 ----
        bmr = 1765 + rnd.gauss(0, 35)
        active = 330 + (210 if weekend else 0) + rnd.gauss(0, 105) - (140 if sick else 0)
        active = max(90.0, active)
        steps = int(max(900, active * rnd.uniform(9.0, 12.5)))
        stress = max(8, min(88, 30 - (sleep_min - 400) / 18.0 + rnd.gauss(0, 8) + (16 if sick else 0)))

        def nul(value):
            return value if not missing else None

        records.append(
            {
                "date": day.isoformat(),
                "sleep": {
                    "score": round(nul(score), 0) if not missing else None,
                    "duration_min": round(sleep_min, 1) if not missing else None,
                    "deep_min": round(deep, 1) if not missing else None,
                    "light_min": round(light, 1) if not missing else None,
                    "rem_min": round(rem, 1) if not missing else None,
                    "awake_min": round(awake, 1) if not missing else None,
                    "spo2": round(rnd.uniform(94, 98), 1) if not missing else None,
                    "respiration": round(rnd.uniform(13.5, 16.5), 1) if not missing else None,
                    "resting_hr": round(52 + rnd.gauss(0, 3)) if not missing else None,
                    "avg_hrv": round(hrv + rnd.gauss(0, 1.5), 1) if not missing else None,
                },
                "hrv": {
                    "nightly_avg": round(hrv, 1) if not missing else None,
                    "weekly_avg": None,
                    "night_high": round(hrv + rnd.uniform(6, 16), 1) if not missing else None,
                    "baseline_low": 39.0,
                    "baseline_high": 56.0,
                    "status": None if missing else ("BALANCED" if 39 <= hrv <= 56 else ("LOW" if hrv < 39 else "HIGH")),
                },
                "calories": {
                    "total": round(bmr + active) if not missing else None,
                    "active": round(active) if not missing else None,
                    "bmr": round(bmr) if not missing else None,
                    "steps": steps if not missing else None,
                    "resting_hr": round(52 + rnd.gauss(0, 3)) if not missing else None,
                    "stress": round(stress) if not missing else None,
                    "body_battery": round(max(5, min(100, 62 + (hrv - 45) * 0.9 + rnd.gauss(0, 7)))) if not missing else None,
                    "intensity_min": round(max(0, active / 8 + rnd.gauss(0, 8))) if not missing else None,
                },
            }
        )
        prev_sleep = sleep_min if not missing else prev_sleep

    # 补 7 日滚动均值（真实 API 里 Garmin 也会给 weeklyAvg）
    for i, rec in enumerate(records):
        window = [
            r["hrv"]["nightly_avg"]
            for r in records[max(0, i - 6) : i + 1]
            if r["hrv"]["nightly_avg"] is not None
        ]
        rec["hrv"]["weekly_avg"] = round(sum(window) / len(window), 1) if window else None

    return records
