"""静态站构建器：把 Garmin 数据 + DeepSeek 解读 + 教练周计划编译成纯 JSON 快照。

对应架构图：
    佳明手表 → 佳明云 → (每日 06:47 读取) → 教练分析 → GitHub 仓库 → Pages 大屏 / Widget

安全约定（公开仓库 / 公开 Actions 日志下尤其重要）：
- 凭证只从环境变量读（GitHub Actions Secrets 注入），不写进任何产物、日志或 commit。
- 原始 API 响应、token、缓存只留在 data/（.gitignore 排除）。
- 日志只打印「状态 / 条数 / 错误码」，绝不打印健康数值、密钥或响应正文。
- 对外的 config 字段清除密钥尾四位提示。

用法：
    python scripts/build_site.py             # 按环境变量拉真实数据（无 Key 时自动降级演示）
    python scripts/build_site.py --demo      # 强制演示数据，用于本地开发与首次占位提交
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# --demo 必须在 config 被 import 之前生效；config._load_env 不会覆盖已有环境变量
_ARGS = argparse.ArgumentParser(add_help=False)
_ARGS.add_argument("--demo", action="store_true")
_ARGS.add_argument("--out", default="")
_ARGS.add_argument("--dry-run", action="store_true")
_known, _ = _ARGS.parse_known_args()
if _known.demo:
    os.environ["DATA_SOURCE"] = "mock"
    os.environ["SITE_FORCE_DEMO"] = "1"

import analyzer  # noqa: E402
import config  # noqa: E402
import llm_interpreter  # noqa: E402
import mock_data  # noqa: E402
import normalizer  # noqa: E402
import build_plan  # noqa: E402
from garmin_client import GarminClient  # noqa: E402

RANGES = (7, 14, 30, 90)
CN_TZ = timezone(timedelta(hours=8))
DATA_OUT = Path(_known.out).resolve() if _known.out else ROOT / "docs" / "data"

COVERAGE_FIELDS = {
    "sleep_score": ("sleep", "score"),
    "sleep_duration": ("sleep", "duration_min"),
    "sleep_deep": ("sleep", "deep_min"),
    "sleep_rem": ("sleep", "rem_min"),
    "hrv": ("hrv", "nightly_avg"),
    "hrv_baseline_low": ("hrv", "baseline_low"),
    "hrv_baseline_high": ("hrv", "baseline_high"),
    "calories_total": ("calories", "total"),
    "calories_active": ("calories", "active"),
    "calories_bmr": ("calories", "bmr"),
    "steps": ("calories", "steps"),
}


def log(msg: str) -> None:
    print(f"[build_site] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_config() -> dict:
    """只暴露非敏感状态；密钥尾四位提示也不发布到公开 Pages。"""
    cfg = config.public_config()
    for key in ("garmin_key_hint", "deepseek_key_hint"):
        cfg.pop(key, None)
    cfg["static_site"] = True
    cfg["garmin_configured"] = bool(config.GARMIN_API_KEY)
    cfg["deepseek_configured"] = bool(config.DEEPSEEK_API_KEY)
    return cfg


def coverage_of(records: list[dict]) -> dict:
    return {
        name: sum(
            1 for r in records
            if isinstance(r.get(group), dict) and r[group].get(field) is not None
        )
        for name, (group, field) in COVERAGE_FIELDS.items()
    }


def fetch_dataset() -> dict:
    """拉 90 天真实数据；任何失败都降级为演示数据并在 meta 里标注原因。"""
    if config.effective_source() == "mock":
        return {
            "source": "mock",
            "records": mock_data.generate(90),
            "coverage": {},
            "errors": {},
            "fallbacks": [],
            "fallback_reason": "演示数据：未配置 GARMIN_PERSONAL_API_KEY 或使用 --demo。",
            "region": None,
            "fetched_at": now_iso(),
        }
    try:
        client = GarminClient()
        raw = client.fetch_range(90)
        if raw.get("auth_error"):
            raise RuntimeError("认证失败（token 交换或地区配置有误）")
        records, coverage = normalizer.normalize(raw)
        if not records:
            raise RuntimeError("响应为空")
        return {
            "source": "garmin",
            "records": records,
            "coverage": coverage,
            "errors": raw.get("errors", {}),
            "fallbacks": raw.get("fallbacks", []),
            "fallback_reason": "",
            "region": raw.get("region"),
            "fetched_at": raw.get("fetched_at", now_iso()),
        }
    except Exception as exc:
        # 不打印异常正文：可能含 URL / 响应片段；只给类型
        reason = f"Garmin 拉取失败（{type(exc).__name__}），本次构建降级为演示数据。"
        log(reason)
        return {
            "source": "mock",
            "records": mock_data.generate(90),
            "coverage": {},
            "errors": {"fetch": {"message": "拉取失败，已降级为演示数据"}},
            "fallbacks": [],
            "fallback_reason": reason,
            "region": None,
            "fetched_at": now_iso(),
        }


def llm_ranges() -> set[int]:
    raw = os.environ.get("SITE_LLM_RANGES", "7,14,30,90").strip()
    if raw.lower() in ("0", "none", "off"):
        return set()
    out = set()
    for part in raw.split(","):
        try:
            v = int(part.strip())
        except ValueError:
            continue
        if v in RANGES:
            out.add(v)
    return out


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build_snapshot(days: int, dataset: dict, cfg: dict, do_llm: bool) -> dict:
    records = dataset["records"][-days:]
    analysis = analyzer.analyze(records, days)
    if do_llm and config.DEEPSEEK_API_KEY:
        interpretation = llm_interpreter.interpret(analysis)
    else:
        interpretation = llm_interpreter._rule(
            analysis,
            "本次构建未调用大模型（未配置 Key 或该时间范围未启用），当前为本地统计规则解读。",
        )
    public_analysis = {k: v for k, v in analysis.items() if k != "llm_payload"}
    cov = coverage_of(records) if dataset["source"] == "garmin" else {}
    return {
        "config": cfg,
        "meta": {
            "source": dataset["source"],
            "days": days,
            "fetched_at": dataset["fetched_at"],
            "generated_at": now_iso(),
            "coverage": {"days": days, "metrics": cov, "raw_counts": {}},
            "errors": dataset["errors"],
            "fallbacks": dataset["fallbacks"],
            "fallback_reason": dataset["fallback_reason"],
            "region": dataset["region"],
            "static": True,
            "offline": False,
        },
        "analysis": public_analysis,
        "interpretation": interpretation,
    }


def build_widget(dataset: dict, snapshots: dict, plan: dict) -> dict:
    base = snapshots[30]["analysis"]
    metrics = base.get("metrics") or {}
    kpi_defs = [
        ("sleep_score", "睡眠评分", "分", 0),
        ("sleep_duration", "睡眠时长", "小时", 1),
        ("hrv", "夜间 HRV", "ms", 1),
        ("calories_total", "总消耗", "kcal", 0),
    ]
    kpis = []
    for key, label, unit, digits in kpi_defs:
        m = metrics.get(key) or {}
        v = m.get("avg7")
        kpis.append({
            "key": key, "label": label, "unit": unit, "digits": digits,
            "value": round(v, digits) if isinstance(v, (int, float)) else None,
            "change_pct": m.get("change_pct"),
        })
    spark = {
        key: [x.get("value") for x in (metrics.get(key) or {}).get("series", [])[-14:]]
        for key, *_ in kpi_defs
    }
    today = plan["days"][0] if plan.get("days") else {}
    note_lines = [ln for ln in (plan.get("coach_note") or "").splitlines() if ln.strip()]
    return {
        "updated_at": dataset["fetched_at"],
        "built_at": now_iso(),
        "source": dataset["source"],
        "status": plan["status"]["label"],
        "kpis": kpis,
        "spark": spark,
        "today_plan": {
            "date": today.get("date"), "weekday": today.get("weekday"),
            "type": today.get("type"), "duration_min": today.get("duration_min"),
            "intensity": today.get("intensity"), "detail": today.get("detail"),
        },
        "coach_line": note_lines[0] if note_lines else "",
        "weekly_goal": plan.get("weekly_goal"),
        "links": {
            "dashboard": "index.html", "coach": "coach.html",
            "ics": "data/plan.ics", "checklist": "data/workout-checklist.md",
        },
    }


def main() -> int:
    args, _ = _ARGS.parse_known_args()
    out_dir = Path(args.out).resolve() if args.out else DATA_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"输出目录 {out_dir.name} · 数据源 {'演示' if os.environ.get('SITE_FORCE_DEMO') else '按环境变量'}")

    dataset = fetch_dataset()
    log(f"数据获取完成：source={dataset['source']}, records={len(dataset['records'])}, errors={len(dataset['errors'])}")

    cfg = public_config()
    use_llm = llm_ranges()
    snapshots: dict[int, dict] = {}
    for days in RANGES:
        snapshots[days] = build_snapshot(days, dataset, cfg, days in use_llm)
        interp_source = snapshots[days]["interpretation"].get("source")
        log(f"快照 {days:>2} 天：records={len(snapshots[days]['analysis']['records'])}, 解读={interp_source}")

    # 手写备注（对应架构图里「你发」的踢球截图 / 留言）；只取文本，不处理图片
    notes_path = ROOT / "inbox" / "NOTES.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""

    analysis30 = analyzer.analyze(dataset["records"][-30:], 30)
    plan = build_plan.build_plan(
        dataset["records"], analysis30,
        interpretation=snapshots[30]["interpretation"], notes_text=notes,
    )
    log(f"周计划：{plan['status']['label']}，{len(plan['days'])} 天，备注={'有' if plan['user_note'] else '无'}")

    if args.dry_run:
        log("dry-run：未写入任何文件")
        return 0

    for days in RANGES:
        write_json(out_dir / f"snapshot-{days}.json", snapshots[days])
        # 演示兜底：真实快照暂缺时页面仍有内容（首次部署即用它占位）
        write_json(out_dir / f"demo-{days}.json", snapshots[days] if dataset["source"] == "mock" else _demo_snapshot(days))

    widget = build_widget(dataset, snapshots, plan)
    write_json(out_dir / "widget.json", widget)
    plan_files = build_plan.write_outputs(plan, out_dir)
    write_json(out_dir / "meta.json", {
        "built_at": now_iso(),
        "built_at_local": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": dataset["source"],
        "region": dataset["region"],
        "records": len(dataset["records"]),
        "ranges": list(RANGES),
        "llm_source": snapshots[30]["interpretation"].get("source"),
        "llm_model": snapshots[30]["interpretation"].get("model"),
        "llm_ranges": sorted(use_llm),
        "coach_status": plan["status"]["label"],
        "schedule": "每天 06:47（Asia/Shanghai）由 GitHub Actions 自动更新，cron 使用 UTC",
        "note": "静态快照，不含任何凭证；公开 Pages 上的健康数据任何人可见，请勿外传 URL。",
        "plan_files": plan_files["files"],
    })
    log(f"完成：{len(list(out_dir.glob('*')))} 个文件已写入")
    return 0


_DEMO_CACHE: dict[int, dict] = {}


def _demo_snapshot(days: int) -> dict:
    """只在真实快照缺失时使用的仿真兜底；结果缓存在进程内。"""
    if days in _DEMO_CACHE:
        return _DEMO_CACHE[days]
    records = mock_data.generate(90)[-days:]
    analysis = analyzer.analyze(records, days)
    interp = llm_interpreter._rule(analysis, "离线兜底：真实快照暂不可用，展示内置演示数据。")
    snap = {
        "config": public_config(),
        "meta": {
            "source": "mock", "days": days, "fetched_at": now_iso(),
            "generated_at": now_iso(),
            "coverage": {"days": days, "metrics": {}, "raw_counts": {}},
            "errors": {}, "fallbacks": [],
            "fallback_reason": "真实快照暂不可用，已自动切换为内置演示数据。",
            "region": None, "static": True, "offline": True,
        },
        "analysis": {k: v for k, v in analysis.items() if k != "llm_payload"},
        "interpretation": interp,
    }
    _DEMO_CACHE[days] = snap
    return snap


if __name__ == "__main__":
    raise SystemExit(main())
