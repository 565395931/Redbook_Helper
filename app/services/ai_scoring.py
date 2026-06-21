from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "prompts" / "scoring_prompt.md"
KIE_URL = "https://api.kie.ai/gemini-3-5-flash-openai/v1/chat/completions"
DEFAULT_KIE_PROXY = "127.0.0.1:7890"
load_dotenv(ROOT / ".env")
load_dotenv()


def load_scoring_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _ratio_score(ratio: float, strong_at: float) -> int:
    if ratio <= 0:
        return 0
    return max(0, min(100, round((ratio / strong_at) * 100)))


def calculate_burst_signal(result: dict[str, Any], like_count: int, collect_count: int, comment_count: int) -> int:
    likes = max(1, int(like_count or 0))
    collect_ratio = collect_count / likes
    comment_ratio = comment_count / likes
    collect_rate_score = _ratio_score(collect_ratio, 1.0)
    comment_rate_score = _ratio_score(comment_ratio, 0.2)
    title_score = int(((result.get("标题") or {}).get("score") or 0) / 25 * 100)
    cover_score = int(((result.get("封面") or {}).get("score") or 0) / 20 * 100)
    return round(collect_rate_score * 0.4 + comment_rate_score * 0.2 + title_score * 0.2 + cover_score * 0.2)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        raise RuntimeError("KIE 未返回评分内容")
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _stream_content(response: requests.Response) -> str:
    pieces: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = raw_line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                pieces.append(delta["content"])
            message = choice.get("message") or {}
            if message.get("content"):
                pieces.append(message["content"])
    return "".join(pieces)


def _json_response_content(payload: dict[str, Any]) -> str:
    code = payload.get("code")
    if code not in (None, 0, 200):
        message = payload.get("msg") or payload.get("message") or "未知错误"
        raise RuntimeError(f"KIE 服务错误（{code}）：{message}")

    pieces: list[str] = []
    for choice in payload.get("choices") or []:
        delta = choice.get("delta") or {}
        message = choice.get("message") or {}
        if delta.get("content"):
            pieces.append(str(delta["content"]))
        if message.get("content"):
            pieces.append(str(message["content"]))
    return "".join(pieces)


def _response_content(response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise RuntimeError("KIE 返回了无法解析的响应") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("KIE 返回格式不正确")
        content = _json_response_content(payload)
    else:
        content = _stream_content(response)

    if not content.strip():
        raise RuntimeError("KIE 未返回评分内容")
    return content


def _validate_result(result: dict[str, Any]) -> None:
    missing = [
        key
        for key in ("封面", "标题", "正文", "情绪价值", "数据表现", "总分", "爆款概率", "优化建议")
        if key not in result
    ]
    if missing:
        raise RuntimeError(f"KIE 评分结果缺少字段：{'、'.join(missing)}")


def _normalize_proxy_url(proxy: str) -> str:
    value = str(proxy or "").strip()
    if not value:
        return ""
    if "://" not in value:
        return f"http://{value}"
    return value


def normalize_score_result(result: dict[str, Any]) -> dict[str, Any]:
    limits = {"封面": 20, "标题": 25, "正文": 35, "情绪价值": 20, "数据表现": 40}
    for key, limit in limits.items():
        item = result.get(key) or {}
        item["score"] = max(0, min(limit, int(item.get("score") or 0)))
        result[key] = item

    content_score = sum(result[key]["score"] for key in ("封面", "标题", "正文", "情绪价值"))
    performance_score = result["数据表现"]["score"]
    result["内容质量总分"] = content_score
    result["总分"] = max(0, min(100, round(content_score * 0.6 + performance_score)))
    return result


def score_note_with_model(
    note: dict[str, Any],
    cover_url: str | None,
    api_token: str = "",
    proxy: str = "",
) -> dict[str, Any]:
    token = api_token.strip() or os.getenv("KIE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("未配置 KIE Key，请先在设置中填写")

    prompt = load_scoring_prompt()
    note_payload = {
        "title": note.get("title") or "",
        "body": note.get("body") or "",
        "like_count": int(note.get("like_count") or 0),
        "collect_count": int(note.get("collect_count") or 0),
        "comment_count": int(note.get("comment_count") or 0),
        "share_count": int(note.get("share_count") or 0),
        "cover_provided": bool(cover_url),
        "published_at": note.get("published_at") or "",
        "publication_age_days": note.get("publication_age_days"),
    }
    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"{prompt}\n\n笔记数据：\n{json.dumps(note_payload, ensure_ascii=False)}"}
    ]
    if cover_url:
        content.append({"type": "image_url", "image_url": {"url": cover_url}})

    payload = {
        "messages": [{"role": "user", "content": content}],
        "tools": [{"type": "function", "function": {"name": "googleSearch"}}],
        "stream": True,
        "include_thoughts": True,
        "reasoning_effort": "high",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    proxy_url = _normalize_proxy_url(proxy or os.getenv("KIE_PROXY", DEFAULT_KIE_PROXY))
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    response = requests.post(KIE_URL, json=payload, headers=headers, proxies=proxies, stream=True, timeout=120)
    response.raise_for_status()

    result = _extract_json(_response_content(response))
    _validate_result(result)
    normalize_score_result(result)
    result.pop("爆款信号", None)
    result["爆款信号"] = calculate_burst_signal(
        result,
        note_payload["like_count"],
        note_payload["collect_count"],
        note_payload["comment_count"],
    )
    return result
