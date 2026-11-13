"""DeepSeek 解读层（OpenAI 兼容格式）。

- 只把聚合统计喂给模型，不发送原始健康明细或任何凭证。
- 结果按 payload 哈希缓存，避免反复刷新烧 token。
- 未配置 Key、网络失败、返回格式异常时，一律降级为规则化统计解读并明确标注来源。
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import config

SYSTEM_PROMPT = """你是一位严谨的运动健康数据分析师。用户会给你一份来自 Garmin 手表的聚合统计 JSON（睡眠、HRV、卡路里消耗）。
要求：
1. 用简体中文，专业但易懂，避免空话和套话，每条结论必须能追溯到给定数值。
2. 只使用给定数据，禁止编造任何数字、日期、指标或个人情况；缺失数据要如实说明"数据不足"。
3. 分别分析睡眠、HRV、卡路里消耗三条趋势，指出变化方向、幅度、异常日与可能的行为关联（如作息、训练负荷），并给出综合建议。
4. 明确区分"描述性统计"与"推测"，推测用"可能""提示"等措辞。不提供医疗诊断，不给用药建议。
5. 严格输出 JSON 对象，不要 markdown 代码块、不要额外文字。字段如下：
{"sleep": "睡眠段落", "hrv": "HRV段落", "calories": "卡路里段落", "overall": "综合建议段落", "highlights": ["要点1", "要点2", "要点3"]}
每段 80-160 字，highlights 3-5 条、每条 20 字以内。"""

CACHE_PATH = config.DATA_DIR / "interpretation_cache.json"


class LLMError(Exception):
    pass


def _rule(analysis: dict, warning: str = "") -> dict:
    return {
        "source": "rule",
        "model": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": analysis.get("rule_sections", {}),
        "highlights": [],
        "warning": warning or "当前为本地统计规则解读（未调用大模型）。",
    }


def _cache_key(payload: dict, model: str) -> str:
    raw = json.dumps({"payload": payload, "model": model}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_cache(key: str) -> dict | None:
    try:
        obj = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if obj.get("key") == key:
            result = dict(obj["result"])
            result["source"] = "cache"
            result["from_cache"] = True
            return result
    except Exception:
        pass
    return None


def _write_cache(key: str, result: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps({"key": key, "result": result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(CACHE_PATH, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
    except Exception:
        pass


def _call(messages: list[dict], model: str, thinking: bool) -> str:
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.35,
        "max_tokens": 1400,
    }
    if thinking:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "high"
    req = urllib.request.Request(
        config.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "User-Agent": "garmin-health-dashboard/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise LLMError(f"DeepSeek 返回 HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise LLMError(f"无法连接 DeepSeek：{exc.reason}") from None
    except TimeoutError:
        raise LLMError("DeepSeek 请求超时") from None
    try:
        obj = json.loads(raw)
        content = obj["choices"][0]["message"].get("content") or ""
    except Exception:
        raise LLMError("DeepSeek 响应格式无法解析") from None
    if not content.strip():
        raise LLMError("DeepSeek 返回空内容")
    return content


def _parse(text: str) -> tuple[dict, list[str], str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if "```" in cleaned:
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"overall": text.strip()}, [], "模型返回非 JSON，已按原文展示。"
    sections = {
        key: str(obj.get(key, "") or "").strip()
        for key in ("sleep", "hrv", "calories", "overall")
    }
    highlights = obj.get("highlights") or []
    if isinstance(highlights, str):
        highlights = [highlights]
    highlights = [str(x).strip() for x in highlights if str(x).strip()][:6]
    return sections, highlights, ""


def test_connection() -> dict:
    """设置面板用的轻量连通性检查：只发一个极短请求，不读取/回显响应正文。"""
    if not config.DEEPSEEK_API_KEY:
        return {"ok": False, "message": "未配置 DeepSeek API Key，请在「设置」里填写后保存。"}
    body = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "max_tokens": 1,
        "temperature": 0,
    }
    req = urllib.request.Request(
        config.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "User-Agent": "garmin-health-dashboard/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=min(config.HTTP_TIMEOUT, 15)) as resp:
            resp.read(256)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "message": f"DeepSeek 返回 HTTP {exc.code}（检查 Key、余额、模型名与 Base URL）",
                "status": exc.code}
    except urllib.error.URLError as exc:
        return {"ok": False, "message": f"无法连接 DeepSeek：{exc.reason}"}
    except TimeoutError:
        return {"ok": False, "message": "DeepSeek 请求超时"}
    except Exception:
        return {"ok": False, "message": "DeepSeek 验证失败"}
    return {"ok": True, "message": "DeepSeek 接口连通（本次仅消耗约 1 token）。",
            "model": config.DEEPSEEK_MODEL, "base": config.DEEPSEEK_BASE_URL}


def interpret(analysis: dict, force: bool = False) -> dict:
    payload = analysis["llm_payload"]
    model = config.DEEPSEEK_MODEL
    key = _cache_key(payload, model)
    if not force:
        cached = _read_cache(key)
        if cached:
            return cached
    if not config.DEEPSEEK_API_KEY:
        return _rule(analysis, "未配置 DEEPSEEK_API_KEY，当前为本地统计规则解读；在页面右上角「设置」中填写 Key 保存后即时生效。")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        text = _call(messages, model, config.DEEPSEEK_THINKING)
        sections, highlights, note = _parse(text)
        result = {
            "source": "deepseek",
            "model": model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": sections,
            "highlights": highlights,
            "warning": note or "",
            "from_cache": False,
        }
        _write_cache(key, result)
        return result
    except LLMError as err:
        return _rule(analysis, f"大模型解读失败（{err}），已降级为本地统计规则解读。")
    except Exception:
        return _rule(analysis, "大模型解读出现未知错误，已降级为本地统计规则解读。")
