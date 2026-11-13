"""配置层。

安全约定（重要）：
- 凭证只从环境变量 / 项目根目录 .env / 权限受限的本机文件读取。
- 凭证绝不写入日志、缓存、源码仓库或 API 响应；对外只暴露脱敏提示。
- 绝不在聊天或提示词里索要密钥。

网页设置面板通过 save_settings() 写入 .env 并调用 reload() 重算模块变量，
因此 garmin_client / llm_interpreter 每次请求读到的都是最新配置，无需重启服务。
"""
from __future__ import annotations

import os
import re
import stat
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
WEB_DIR = ROOT / "web"
ENV_PATH = ROOT / ".env"

_LOCK = threading.RLock()

# 网页设置面板允许修改的键；PORT 不在其中（改端口必须重启服务）。
EDITABLE_KEYS = (
    "GARMIN_PERSONAL_API_KEY",
    "GARMIN_REGION",
    "GARMIN_API_BASE",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_THINKING",
    "DATA_SOURCE",
    "DEFAULT_DAYS",
    "CACHE_TTL_MINUTES",
    "HTTP_TIMEOUT",
)
SECRET_KEYS = ("GARMIN_PERSONAL_API_KEY", "DEEPSEEK_API_KEY")
GARMIN_KEYS = ("GARMIN_PERSONAL_API_KEY", "GARMIN_REGION", "GARMIN_API_BASE", "DATA_SOURCE")
DEEPSEEK_KEYS = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_THINKING")

# 前端约定：密钥留空 = 保持原值；发送此标记 = 明确清空。
CLEAR_MARK = "__CLEAR__"

# Garmin Connect Personal API 的地区主机。中国区账号需使用 garmin.cn 域。
REGIONS = {
    "global": "https://personal.connectapi.garmin.com",
    "cn": "https://personal.connectapi.garmin.cn",
}
REGION_LABELS = {
    "global": "全球区 · personal.connectapi.garmin.com",
    "cn": "中国区 · personal.connectapi.garmin.cn",
}
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"

MODEL_CHOICES = (
    {"value": "deepseek-flash", "label": "deepseek-flash · 快速 / 默认"},
    {"value": "deepseek-v4-pro", "label": "deepseek-v4-pro · 更强 / 更贵"},
)


class SettingsError(Exception):
    """配置校验失败，消息可安全展示给用户（不含凭证）。"""


def _parse_env_line(line: str):
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    return key.strip(), value.strip().strip('"').strip("'")


def _load_env(path: Path, force_keys: set[str] | None = None) -> None:
    """读取 .env。默认不覆盖已有进程环境变量；reload 时只强制覆盖白名单字段。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if not parsed:
            continue
        key, value = parsed
        if not key:
            continue
        if key in (force_keys or set()) or key not in os.environ:
            os.environ[key] = value


def _secret(env_name: str, file_env_name: str = "") -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    if file_env_name:
        path = os.environ.get(file_env_name, "").strip()
        if path:
            f = Path(path).expanduser()
            if f.exists():
                return f.read_text(encoding="utf-8").strip()
    return ""


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _recompute() -> None:
    """重算所有模块级配置常量；由 import 与 reload() 共用。"""
    global GARMIN_API_KEY, DEEPSEEK_API_KEY, GARMIN_REGION, GARMIN_API_BASE
    global DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_THINKING
    global DATA_SOURCE, CACHE_TTL_MINUTES, DEFAULT_DAYS, PORT, HTTP_TIMEOUT

    GARMIN_API_KEY = _secret("GARMIN_PERSONAL_API_KEY", "GARMIN_PERSONAL_API_KEY_FILE")
    DEEPSEEK_API_KEY = _secret("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_FILE")

    GARMIN_REGION = os.environ.get("GARMIN_REGION", "global").strip().lower()
    if GARMIN_REGION not in REGIONS:
        GARMIN_REGION = "global"
    GARMIN_API_BASE = (
        os.environ.get("GARMIN_API_BASE", "").strip().rstrip("/") or REGIONS[GARMIN_REGION]
    )

    DEEPSEEK_BASE_URL = (
        os.environ.get("DEEPSEEK_BASE_URL", "").strip().rstrip("/") or "https://api.deepseek.com"
    )
    DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash"
    DEEPSEEK_THINKING = _bool("DEEPSEEK_THINKING")

    # auto = 有 Garmin Key 就走真实 API，否则自动降级到演示数据
    DATA_SOURCE = os.environ.get("DATA_SOURCE", "auto").strip().lower()
    if DATA_SOURCE not in ("auto", "garmin", "mock"):
        DATA_SOURCE = "auto"
    CACHE_TTL_MINUTES = max(1, _int("CACHE_TTL_MINUTES", 120))
    DEFAULT_DAYS = _int("DEFAULT_DAYS", 30)
    if DEFAULT_DAYS not in (7, 14, 30, 90):
        DEFAULT_DAYS = 30
    PORT = _int("PORT", 8765)
    HTTP_TIMEOUT = max(3, _int("HTTP_TIMEOUT", 20))


MAX_DAYS = 90
_load_env(ENV_PATH)
_recompute()


def reload() -> dict:
    """从 .env 重新加载白名单字段并立即生效（不重启进程）。"""
    with _LOCK:
        _load_env(ENV_PATH, force_keys=set(EDITABLE_KEYS))
        _recompute()
        return public_config()


def effective_source() -> str:
    if DATA_SOURCE == "mock":
        return "mock"
    return "garmin" if GARMIN_API_KEY else "mock"


def _hint(value: str) -> str:
    return ("*" * 8 + value[-4:]) if len(value) > 4 else ("已设置" if value else "")


def public_config() -> dict:
    """给前端看的状态，不含任何可用凭证。"""
    return {
        "source": effective_source(),
        "configured_source": DATA_SOURCE,
        "garmin_configured": bool(GARMIN_API_KEY),
        "garmin_region": GARMIN_REGION,
        "garmin_api_base": GARMIN_API_BASE,
        "garmin_base_custom": bool(os.environ.get("GARMIN_API_BASE", "").strip()),
        "garmin_key_hint": _hint(GARMIN_API_KEY),
        "deepseek_configured": bool(DEEPSEEK_API_KEY),
        "deepseek_model": DEEPSEEK_MODEL,
        "deepseek_base_url": DEEPSEEK_BASE_URL,
        "deepseek_key_hint": _hint(DEEPSEEK_API_KEY),
        "cache_ttl_minutes": CACHE_TTL_MINUTES,
        "default_days": DEFAULT_DAYS,
        "max_days": MAX_DAYS,
        "port": PORT,
    }


def settings_public() -> dict:
    """设置面板初值。密钥只给是否已配置与尾四位提示，绝不回传明文。"""
    return {
        "values": {
            "GARMIN_REGION": GARMIN_REGION,
            "GARMIN_API_BASE": os.environ.get("GARMIN_API_BASE", "").strip(),
            "DEEPSEEK_BASE_URL": DEEPSEEK_BASE_URL,
            "DEEPSEEK_MODEL": DEEPSEEK_MODEL,
            "DEEPSEEK_THINKING": DEEPSEEK_THINKING,
            "DATA_SOURCE": DATA_SOURCE,
            "DEFAULT_DAYS": DEFAULT_DAYS,
            "CACHE_TTL_MINUTES": CACHE_TTL_MINUTES,
            "HTTP_TIMEOUT": HTTP_TIMEOUT,
        },
        "secrets": {
            "GARMIN_PERSONAL_API_KEY": {"configured": bool(GARMIN_API_KEY), "hint": _hint(GARMIN_API_KEY)},
            "DEEPSEEK_API_KEY": {"configured": bool(DEEPSEEK_API_KEY), "hint": _hint(DEEPSEEK_API_KEY)},
        },
        "regions": [{"value": k, "label": REGION_LABELS[k], "base": v} for k, v in REGIONS.items()],
        "models": list(MODEL_CHOICES),
        "clear_mark": CLEAR_MARK,
        "env_path": str(ENV_PATH),
        "env_exists": ENV_PATH.exists(),
        "env_writable": _env_writable(),
        "port": PORT,
        "note": "保存后立即生效并自动重新拉取数据，无需重启服务。PORT 为启动参数，需重启才生效。",
    }


def _env_writable() -> bool:
    if ENV_PATH.exists():
        return os.access(ENV_PATH, os.W_OK)
    return os.access(ROOT, os.W_OK)


# ---------------- 校验 ----------------
_URL_RE = re.compile(r"^https?://[^\s\"'<>]+$", re.I)
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,80}$")
_SECRET_RE = re.compile(r"^\S{1,600}$")


def _validate(key: str, value: str) -> str:
    value = str(value).strip()
    if key in SECRET_KEYS:
        if not _SECRET_RE.match(value):
            raise SettingsError(f"{key} 格式不合法（不能含空格、换行或超过 600 字符）")
        return value
    if key == "GARMIN_REGION":
        if value not in REGIONS:
            raise SettingsError("GARMIN_REGION 只能是 global 或 cn")
        return value
    if key in ("GARMIN_API_BASE", "DEEPSEEK_BASE_URL"):
        if not value:
            return ""
        if not _URL_RE.match(value) or len(value) > 300:
            raise SettingsError(f"{key} 必须是 http(s):// 开头的合法地址")
        return value.rstrip("/")
    if key == "DEEPSEEK_MODEL":
        if not _MODEL_RE.match(value):
            raise SettingsError("DEEPSEEK_MODEL 含不支持的字符")
        return value
    if key == "DEEPSEEK_THINKING":
        return "1" if str(value).strip().lower() in ("1", "true", "yes", "on") else "0"
    if key == "DATA_SOURCE":
        if value not in ("auto", "garmin", "mock"):
            raise SettingsError("DATA_SOURCE 只能是 auto / garmin / mock")
        return value
    ranges = {"DEFAULT_DAYS": (7, 90), "CACHE_TTL_MINUTES": (1, 1440), "HTTP_TIMEOUT": (3, 120)}
    if key in ranges:
        try:
            number = int(value)
        except ValueError:
            raise SettingsError(f"{key} 必须是整数") from None
        lo, hi = ranges[key]
        if not lo <= number <= hi:
            raise SettingsError(f"{key} 需在 {lo}–{hi} 之间")
        if key == "DEFAULT_DAYS" and number not in (7, 14, 30, 90):
            raise SettingsError("DEFAULT_DAYS 只能是 7 / 14 / 30 / 90")
        return str(number)
    raise SettingsError(f"不支持修改的配置项：{key}")


def _write_env(updates: dict[str, str]) -> None:
    """保留注释与未知行，替换/追加白名单键；原子写 + 仅所有者可读写。"""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else [
        "# Garmin 健康趋势看板 · 本机配置（由网页设置面板维护）",
        "# 本文件含密钥，请勿提交到 Git 或分享给他人。",
    ]
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        parsed = _parse_env_line(line)
        if parsed and parsed[0] in remaining:
            key = parsed[0]
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining:
        out.append("")
        out.append("# ---- 由看板设置面板补充 ----")
        out.extend(f"{k}={v}" for k, v in remaining.items())
    text = "\n".join(out).rstrip() + "\n"
    tmp = ROOT / ".env.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, ENV_PATH)
    try:
        os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass  # Windows 上 chmod 语义有限


def save_settings(payload: dict) -> dict:
    """校验并写入 .env，然后立刻 reload 让本进程生效。返回变更摘要。"""
    if not isinstance(payload, dict):
        raise SettingsError("请求格式不正确")
    if not _env_writable():
        raise SettingsError(f"配置文件不可写：{ENV_PATH}")

    with _LOCK:
        updates: dict[str, str] = {}
        for key, raw in payload.items():
            if key not in EDITABLE_KEYS:
                continue
            if key in SECRET_KEYS:
                # 留空 = 保持不变；显式标记 = 清空
                if raw is None or str(raw).strip() == "":
                    continue
                if str(raw).strip() == CLEAR_MARK:
                    updates[key] = ""
                    continue
                updates[key] = _validate(key, str(raw).strip())
            else:
                if raw is None or str(raw).strip() == CLEAR_MARK:
                    updates[key] = ""
                else:
                    updates[key] = _validate(key, str(raw).strip())

        # 切换地区时，若未同时提交自定义主机，就丢弃旧主机，避免残留覆盖新地区
        if "GARMIN_REGION" in updates and "GARMIN_API_BASE" not in updates:
            updates["GARMIN_API_BASE"] = ""

        if not updates:
            return {"changed": [], "reset_garmin": False, "reset_deepseek": False}

        _write_env(updates)
        for key, value in updates.items():
            if value == "":
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        result = reload()
        return {
            "changed": sorted(updates.keys()),
            "reset_garmin": any(k in GARMIN_KEYS for k in updates),
            "reset_deepseek": any(k in DEEPSEEK_KEYS for k in updates),
            "config": result,
        }


def setup_guide() -> dict:
    return {
        "garmin_key_url": (
            "https://connect.garmin.cn/app/settings/personalApiToken"
            if GARMIN_REGION == "cn"
            else "https://connect.garmin.com/app/settings/personalApiToken"
        ),
        "swagger_url": "https://developer.garmin.com/gc-developer-program/personal/swagger-docs/",
        "faq_url": "https://developer.garmin.com/gc-developer-program/personal/personal-faq",
        "deepseek_keys_url": "https://platform.deepseek.com/api_keys",
        "steps": [
            "点击页面右上角「设置」，直接在网页里填写 API Key 与地区，保存后立即生效，无需重启服务",
            "Garmin Connect 网页版 → 设置 → Personal API Key → Create Key（Key 只显示一次，有效期建议 365 天）",
            "中国区账号把「API 地区」切到 中国区（自动使用 personal.connectapi.garmin.cn 主机）",
            "在同一面板填写 DeepSeek API Key 并选择模型，AI 解读随即切换为大模型输出",
            "点「测试连接」验证 token 交换；点「刷新数据」按新配置重新拉取",
        ],
    }
