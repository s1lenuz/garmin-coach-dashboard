"""生成 web/demo-data.js：让看板在没有后端、直接用 file:// 打开时也能完整运行。

预生成 7/14/30/90 天四套完整响应，与真实 API 响应结构完全一致。
运行：python build_demo.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import analyzer
import config
import llm_interpreter
import mock_data


def build() -> None:
    now = datetime.now(timezone.utc).isoformat()
    cfg = config.public_config()
    cfg.update({
        "source": "mock",
        "configured_source": "mock",
        "garmin_configured": False,
        "deepseek_configured": False,
        "garmin_key_hint": "",
        "deepseek_key_hint": "",
    })
    all_records = mock_data.generate(90)
    result: dict[str, dict] = {}
    for days in (7, 14, 30, 90):
        records = all_records[-days:]
        analysis = analyzer.analyze(records, days)
        interpretation = llm_interpreter._rule(
            analysis,
            "离线演示模式：当前为本地统计规则解读；配置 DEEPSEEK_API_KEY 并启动服务后即可获得大模型解读。",
        )
        public_analysis = {k: v for k, v in analysis.items() if k != "llm_payload"}
        result[str(days)] = {
            "config": cfg,
            "meta": {
                "source": "mock",
                "days": days,
                "fetched_at": now,
                "coverage": {"days": days, "metrics": {}, "raw_counts": {}},
                "errors": {},
                "fallbacks": [],
                "fallback_reason": "离线演示模式：未连接本地服务，展示内置示例数据。",
                "region": None,
                "generated_at": now,
                "offline": True,
            },
            "analysis": public_analysis,
            "interpretation": interpretation,
            "guide": config.setup_guide(),
        }
    target = config.WEB_DIR / "demo-data.js"
    target.write_text(
        "window.DEMO_DATA = " + json.dumps(result, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(f"已生成 {target}  ({target.stat().st_size:,} bytes)  覆盖 7/14/30/90 天")


if __name__ == "__main__":
    build()
