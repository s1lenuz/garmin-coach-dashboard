"""Garmin Connect Personal API 官方客户端（只读）。

认证流程（官方 FAQ）：
  POST {base}/personalapitoken-service/personal-api/personal-api-key/token
  Header: Authorization: ApiKey <Personal API Key>
  -> access_token（24 小时有效，无 refresh token；过期用同一个 Key 重新换取）
业务请求全部改用 Authorization: Bearer <access_token>。

只调用官方 Swagger 文档化的 GET 端点；不碰任何私有/未文档化接口。
"""
from __future__ import annotations

import base64
import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config

USER_AGENT = "garmin-health-dashboard/1.0 (personal read-only)"

# 官方 token 端点返回的是纯文本 JWT（不是 JSON）。若请求带
# `Accept: application/json`，Garmin 的严格内容协商会返回 406 Not Acceptable；
# 官方 FAQ 的 curl 示例只带 Authorization，不带任何 Accept 头。
# 业务端点一般接受 application/json，但个别部署同样会在 406 时要求放宽，
# 因此这里统一做「原 Accept → */*」的一次自动重试。
TOKEN_ACCEPT = None
DEFAULT_ACCEPT = "application/json"
FALLBACK_ACCEPT = "*/*"

ERROR_HINTS = {
    406: "内容协商失败（HTTP 406）：token 端点返回纯文本 JWT，不接受 Accept: application/json。客户端已自动放宽 Accept 重试；若仍失败请检查 Key 与地区主机。",
    401: "认证失败：Key 可能无效、已撤销或已过期；中国区账号还需在「设置」里把地区切到「中国区」。",
    403: "访问被拒绝：确认 Personal API Key 权限与账号地区。",
    404: "端点不存在：确认地区主机（全球区 / 中国区）与端点路径。",
}


class GarminError(Exception):
    """可安全对外展示的错误：不含凭证、不含远端响应正文。"""

    def __init__(self, message: str, status: int | None = None, path: str = "", hint: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.path = path
        self.hint = hint or (ERROR_HINTS.get(status, "") if status else "")

    def public(self) -> dict:
        return {"message": self.message, "status": self.status,
                "path": self.path, "hint": self.hint}


def _jwt_exp(token: str) -> float | None:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part).decode("utf-8"))
        exp = payload.get("exp")
        return float(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


class GarminClient:
    def __init__(self, api_key: str = "", base: str = "", token_cache: str = ""):
        self.api_key = api_key or config.GARMIN_API_KEY
        if not self.api_key:
            raise GarminError("未配置 GARMIN_PERSONAL_API_KEY")
        self.base = (base or config.GARMIN_API_BASE).rstrip("/")
        self.cache_path = Path(token_cache) if token_cache else config.DATA_DIR / "token_cache.json"
        self._token = ""
        self._exp = 0.0
        self._load_cached_token()

    # ---------- token ----------
    def _load_cached_token(self) -> None:
        try:
            obj = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if obj.get("base") == self.base and float(obj.get("exp", 0)) - 60 > time.time():
                self._token = obj.get("token", "")
                self._exp = float(obj.get("exp", 0))
        except Exception:
            pass

    def _save_cached_token(self, token: str, exp: float) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({"base": self.base, "token": token, "exp": exp}), encoding="utf-8"
            )
            try:
                os.chmod(self.cache_path, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass  # Windows 上 chmod 语义有限，忽略
        except Exception:
            pass

    def _http(self, url: str, method: str = "GET", headers: dict | None = None,
              accept: str | None = DEFAULT_ACCEPT) -> tuple[int, str]:
        """发送单个请求。406 时用 `Accept: */*` 自动重试一次。

        accept=None 表示完全不发送 Accept 头（token 端点必须如此）。
        """
        attempts = [accept, FALLBACK_ACCEPT] if accept != FALLBACK_ACCEPT else [accept]
        attempts = list(dict.fromkeys(attempts))  # 保留顺序去重，None 也在其中
        for index, want in enumerate(attempts):
            final_headers = {"User-Agent": USER_AGENT}
            if headers:
                final_headers.update(headers)
            final_headers.pop("Accept", None)
            if want:
                final_headers["Accept"] = want
            req = urllib.request.Request(url, method=method, headers=final_headers)
            try:
                with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
                    return resp.status, resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                # 不读取、不回显响应正文，避免泄露个人健康数据
                if exc.code == 406 and index < len(attempts) - 1:
                    continue  # 内容协商失败，放宽 Accept 再试一次
                raise GarminError(f"HTTP {exc.code}", status=exc.code, path=url) from None
            except urllib.error.URLError as exc:
                raise GarminError(f"网络不可达：{exc.reason}", path=url) from None
            except TimeoutError:
                raise GarminError("请求超时", path=url) from None
        raise GarminError("请求失败（内容协商未成功）", path=url)

    def access_token(self) -> str:
        if self._token and self._exp - 60 > time.time():
            return self._token
        url = self.base + config.TOKEN_PATH
        _status, raw = self._http(
            url, method="POST",
            headers={"Authorization": f"ApiKey {self.api_key}"},
            accept=TOKEN_ACCEPT,  # 官方示例不带 Accept；返回体是纯文本 JWT
        )
        stripped = raw.strip()
        token = ""
        exp: float | None = None
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                obj = {}
            token = (
                obj.get("access_token")
                or obj.get("accessToken")
                or obj.get("token")
                or obj.get("accessTokenValue")
                or ""
            )
            expires = obj.get("expires_in") or obj.get("expiresIn")
            if isinstance(expires, (int, float)):
                exp = time.time() + float(expires)
        else:
            token = stripped.strip('"')  # 兼容直接返回纯 JWT 文本
        if not token:
            raise GarminError("token 交换未返回访问令牌（响应格式与预期不一致）", path=url)
        if exp is None:
            exp = _jwt_exp(token) or (time.time() + 20 * 3600)
        exp = min(exp, time.time() + 23 * 3600)  # 保守上限，避免踩到 24h 边界
        self._token, self._exp = token, exp
        self._save_cached_token(token, exp)
        return token

    # ---------- 业务请求 ----------
    def get(self, path: str, params: dict | None = None, _retry: bool = True):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {self.access_token()}"}
        try:
            _status, raw = self._http(url, headers=headers)
        except GarminError as err:
            if err.status == 401 and _retry:
                self._token, self._exp = "", 0.0
                return self.get(path, params, _retry=False)
            raise
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_parse_error": True, "_text": raw[:1500]}

    def profile(self):
        """最小只读连通性检查。"""
        return self.get("/userprofile-service/userprofile/settings")

    # ---------- 批量拉取 ----------
    DAILY_ENDPOINTS = {
        "sleep": lambda d: ("/sleep-service/sleep/dailySleep", {"date": d}),
        "hrv": lambda d: (f"/hrv-service/hrv/{d}", {}),
        "summary": lambda d: ("/usersummary-service/usersummary/daily", {"calendarDate": d}),
    }

    def _daily_fallback(self, kind: str, start: date, end: date) -> dict:
        """范围端点不可用时，逐日调用官方单日端点（限并发）。"""
        dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        path_builder = self.DAILY_ENDPOINTS[kind]

        def one(day: str):
            path, params = path_builder(day)
            try:
                data = self.get(path, params)
            except GarminError:
                return {"_date": day, "_error": True}
            if isinstance(data, dict):
                data = dict(data)
                data["_date"] = day
            elif isinstance(data, list):
                data = [{"_date": day, "_value": x} for x in data]
            return data if data else {"_date": day, "_error": True}

        with ThreadPoolExecutor(max_workers=4) as pool:
            items = list(pool.map(one, dates))
        return {"_daily": items, "_fallback": True}

    def fetch_range(self, days: int = 30) -> dict:
        """拉取睡眠 / HRV / 日汇总三类原始数据，保留原始 JSON 供字段核对。"""
        end = date.today()
        start = end - timedelta(days=days - 1)
        s, e = start.isoformat(), end.isoformat()
        calls = {
            "sleep": ("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": s, "endDate": e}),
            "hrv": (f"/hrv-service/hrv/daily/{s}/{e}", {}),
            "summary": ("/usersummary-service/v2/dailysummaries", {"startDate": s, "endDate": e}),
        }
        # 先验证认证链路。token 交换失败时不做逐日降级——那只会把同一个错误
        # 重复几十次，还会在界面上误报成「端点参数不对」。
        try:
            self.access_token()
        except GarminError as err:
            info = err.public()
            info["stage"] = "auth"
            return {
                "start": s, "end": e, "days": days,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "region": config.GARMIN_REGION,
                "payloads": {}, "errors": {"auth": info},
                "fallbacks": [], "auth_error": True,
            }

        payloads: dict = {}
        errors: dict = {}
        fallbacks: list[str] = []
        for kind, (path, params) in calls.items():
            try:
                payloads[kind] = self.get(path, params)
            except GarminError as err:
                errors[kind] = err.public()
                payloads[kind] = self._daily_fallback(kind, start, end)
                fallbacks.append(kind)
        return {
            "start": s,
            "end": e,
            "days": days,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "region": config.GARMIN_REGION,
            "payloads": payloads,
            "errors": errors,
            "fallbacks": fallbacks,
        }


def save_raw(result: dict) -> Path:
    """把原始响应落到本地，便于核对真实字段名（不含凭证）。"""
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = config.RAW_DIR / f"garmin_{stamp}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = config.RAW_DIR / "latest.json"
    latest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
