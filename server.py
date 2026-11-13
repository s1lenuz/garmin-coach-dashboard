"""本地 HTTP 服务：看板静态资源 + 数据聚合 API。

只监听 127.0.0.1，凭证与健康数据不出本机。
运行：python server.py
"""
from __future__ import annotations

import json
import mimetypes
import os
import stat
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import analyzer
import config
import garmin_client
import llm_interpreter
import mock_data
import normalizer
from garmin_client import GarminClient, GarminError

CACHE_PATH = config.DATA_DIR / "cache.json"
RAW_LATEST = config.RAW_DIR / "latest.json"
TOKEN_CACHE = config.DATA_DIR / "token_cache.json"
INTERP_CACHE = config.DATA_DIR / "interpretation_cache.json"

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/charts.js": ("charts.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/demo-data.js": ("demo-data.js", "text/javascript; charset=utf-8"),
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
}


def _drop(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def invalidate(reset_garmin: bool, reset_deepseek: bool) -> None:
    """配置变更后丢弃派生缓存，避免旧 Key / 旧地区的数据被继续展示。"""
    if reset_garmin:
        _drop(TOKEN_CACHE)   # 换 Key 或换地区后，旧 Bearer token 必然失效
        _drop(CACHE_PATH)    # 旧地区的聚合数据不能混进新地区
        _drop(RAW_LATEST)    # 「查看原始响应」也应对应新配置
    if reset_deepseek:
        _drop(INTERP_CACHE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
    except Exception:
        pass


def _load_cache() -> dict | None:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_age(cached: dict) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
    except Exception:
        return float("inf")


def _mock_dataset(days: int, reason: str = "", errors: dict | None = None) -> dict:
    records = mock_data.generate(90)[-days:]
    return {
        "source": "mock",
        "records": records,
        "days": days,
        "coverage": {"days": days, "metrics": {}, "raw_counts": {}},
        "errors": errors or {},
        "fallbacks": [],
        "fallback_reason": reason,
        "region": None,
        "fetched_at": _now(),
    }


def get_dataset(days: int, refresh: bool = False) -> dict:
    source = config.effective_source()
    if source == "mock":
        reason = "" if config.DATA_SOURCE == "mock" else "未配置 Garmin Personal API Key，当前展示演示数据。"
        return _mock_dataset(days, reason)

    cached = _load_cache()
    if cached and cached.get("days") == days and not refresh and _cache_age(cached) < config.CACHE_TTL_MINUTES * 60:
        return cached

    try:
        client = GarminClient()
        raw = client.fetch_range(days)
        garmin_client.save_raw(raw)

        # 认证阶段失败：不写缓存、不做逐日降级，给出可操作的原因
        if raw.get("auth_error"):
            auth = (raw.get("errors") or {}).get("auth", {})
            reason = (
                f"Garmin 认证失败（{auth.get('message') or 'token 交换失败'}）："
                f"{auth.get('hint') or '请在「设置」里核对 Key 与地区，再用「测试 Garmin」验证。'}"
                " 已降级为演示数据。"
            )
            if cached and cached.get("records"):
                stale = dict(cached)
                stale["fallback_reason"] = reason
                stale["errors"] = raw.get("errors", {})
                return stale
            return _mock_dataset(days, reason, raw.get("errors", {}))

        records, coverage = normalizer.normalize(raw)
        dataset = {
            "source": "garmin",
            "records": records,
            "days": days,
            "coverage": coverage,
            "errors": raw.get("errors", {}),
            "fallbacks": raw.get("fallbacks", []),
            "fallback_reason": "",
            "region": raw.get("region"),
            "fetched_at": raw.get("fetched_at", _now()),
        }
        _safe_write(CACHE_PATH, json.dumps(dataset, ensure_ascii=False))
        return dataset
    except Exception as exc:
        message = getattr(exc, "message", None) or type(exc).__name__
        if cached and cached.get("records"):
            stale = dict(cached)
            stale["fallback_reason"] = f"Garmin 拉取失败（{message}），展示上次缓存数据。"
            return stale
        return _mock_dataset(days, f"Garmin 拉取失败（{message}），已降级为演示数据。")


class Handler(BaseHTTPRequestHandler):
    server_version = "GarminHealthDashboard/1.0"

    def log_message(self, fmt, *args):
        # 不打印查询串与任何响应内容
        try:
            print(f"  {self.address_string()}  {str(args[0]).split('?')[0]}")
        except Exception:
            pass

    def _json(self, obj, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, filename: str, ctype: str) -> None:
        path = config.WEB_DIR / filename
        if not path.exists():
            self._json({"error": f"缺少静态文件 {filename}"}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ---------------- GET ----------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if path in STATIC:
            filename, ctype = STATIC[path]
            self._static(filename, ctype)
            return
        if path == "/api/config":
            self._json({**config.public_config(), "guide": config.setup_guide()})
            return
        if path == "/api/settings":
            self._json({"ok": True, "settings": config.settings_public(), "config": config.public_config()})
            return
        if path == "/api/dashboard":
            try:
                days = int(query.get("days", [config.DEFAULT_DAYS])[0])
            except (TypeError, ValueError):
                days = config.DEFAULT_DAYS
            days = max(1, min(config.MAX_DAYS, days))
            refresh = query.get("refresh", ["0"])[0] in ("1", "true", "yes")
            dataset = get_dataset(days, refresh)
            analysis = analyzer.analyze(dataset["records"], days)
            interpretation = llm_interpreter.interpret(analysis)
            public_analysis = {k: v for k, v in analysis.items() if k != "llm_payload"}
            self._json({
                "config": config.public_config(),
                "meta": {
                    "source": dataset["source"],
                    "days": days,
                    "fetched_at": dataset["fetched_at"],
                    "coverage": dataset["coverage"],
                    "errors": dataset["errors"],
                    "fallbacks": dataset["fallbacks"],
                    "fallback_reason": dataset["fallback_reason"],
                    "region": dataset["region"],
                    "generated_at": _now(),
                },
                "analysis": public_analysis,
                "interpretation": interpretation,
                "guide": config.setup_guide(),
            })
            return
        if path == "/api/raw":
            if not RAW_LATEST.exists():
                self._json({"available": False, "message": "尚无原始响应（当前可能是演示数据模式）。"})
                return
            try:
                obj = json.loads(RAW_LATEST.read_text(encoding="utf-8"))
            except Exception:
                self._json({"available": False, "message": "原始响应解析失败。"})
                return
            preview = json.dumps(obj.get("payloads", {}), ensure_ascii=False, indent=2)
            self._json({
                "available": True,
                "file": "data/raw/latest.json",
                "fetched_at": obj.get("fetched_at"),
                "region": obj.get("region"),
                "errors": obj.get("errors", {}),
                "fallbacks": obj.get("fallbacks", []),
                "shape": {k: type(v).__name__ for k, v in obj.get("payloads", {}).items()},
                "preview": preview[:16000],
                "truncated": len(preview) > 16000,
            })
            return
        self._json({"error": "Not Found", "path": path}, 404)

    # ---------------- POST ----------------
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        try:
            posted = json.loads(body) if body else {}
        except json.JSONDecodeError:
            posted = {}

        if path == "/api/settings":
            payload = posted.get("settings", posted)
            try:
                result = config.save_settings(payload)
            except config.SettingsError as err:
                self._json({"ok": False, "message": str(err)}, 400)
                return
            except OSError as err:
                self._json({"ok": False, "message": f"配置文件写入失败：{type(err).__name__}"}, 500)
                return
            invalidate(result["reset_garmin"], result["reset_deepseek"])
            changed = result["changed"]
            self._json({
                "ok": True,
                "message": f"已保存 {len(changed)} 项配置，立即生效，无需重启服务。" if changed else "没有需要修改的配置。",
                **result,
            })
            return
        if path == "/api/test":
            target = str(posted.get("target") or "garmin").strip().lower()
            self._json(
                llm_interpreter.test_connection() if target == "deepseek" else self._test_connection()
            )
            return
        if path == "/api/interpret":
            try:
                days = int(posted.get("days") or query.get("days", [config.DEFAULT_DAYS])[0])
            except (TypeError, ValueError):
                days = config.DEFAULT_DAYS
            days = max(1, min(config.MAX_DAYS, days))
            dataset = get_dataset(days)
            analysis = analyzer.analyze(dataset["records"], days)
            self._json({"interpretation": llm_interpreter.interpret(analysis, force=True)})
            return
        self._json({"error": "Not Found", "path": path}, 404)

    @staticmethod
    def _test_connection() -> dict:
        if not config.GARMIN_API_KEY:
            return {"ok": False, "message": "未配置 Garmin Personal API Key，请在页面右上角「设置」中填写并保存。"}
        common = {"region": config.GARMIN_REGION, "base": config.GARMIN_API_BASE}
        try:
            client = GarminClient()
            client.access_token()
        except GarminError as err:
            return {"ok": False, "stage": "token", "message": f"token 交换失败：{err.message}",
                    "hint": err.hint, "status": err.status, **common}
        except Exception as exc:
            return {"ok": False, "stage": "token", "message": f"token 交换异常：{type(exc).__name__}", **common}
        try:
            client.profile()
        except GarminError as err:
            return {"ok": False, "stage": "profile", "message": f"业务端点验证失败：{err.message}",
                    "hint": err.hint, "status": err.status, **common}
        except Exception as exc:
            return {"ok": False, "stage": "profile", "message": f"业务端点异常：{type(exc).__name__}", **common}
        return {"ok": True, "stage": "ok",
                "message": "token 交换与 profile 只读验证成功，可以正常拉取数据。", **common}


def main() -> None:
    print("=" * 68)
    print("  Garmin 健康趋势看板  |  睡眠 / HRV / 卡路里  +  DeepSeek 解读")
    print("=" * 68)
    print(f"  数据源        : {config.effective_source()} ({config.DATA_SOURCE})")
    print(f"  Garmin Key    : {'已配置 ' + config.public_config()['garmin_key_hint'] if config.GARMIN_API_KEY else '未配置'}")
    print(f"  Garmin 主机   : {config.GARMIN_API_BASE}")
    print(f"  DeepSeek Key  : {'已配置' if config.DEEPSEEK_API_KEY else '未配置（使用规则化解读）'}")
    print(f"  DeepSeek 模型 : {config.DEEPSEEK_MODEL}")
    print(f"  访问地址      : http://127.0.0.1:{config.PORT}")
    print(f"  网页设置面板  : 页面右上角「设置」→ 填 Key / 切地区 → 保存立即生效")
    print(f"  配置文件      : {config.ENV_PATH}")
    print("=" * 68)
    print("  Ctrl+C 停止服务\n")
    httpd = ThreadingHTTPServer(("127.0.0.1", config.PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
