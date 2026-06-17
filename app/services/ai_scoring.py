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
    title_score = int(((result.get("??") or {}).get("score") or 0) / 25 * 100)
    cover_score = int(((result.get("??") or {}).get("score") or 0) / 20 * 100)
    return round(collect_rate_score * 0.4 + comment_rate_score * 0.2 + title_score * 0.2 + cover_score * 0.2)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
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


def score_note_with_model(note: dict[str, Any], cover_url: str | None) -> dict[str, Any]:
    token = os.getenv("KIE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("?????? KIE_API_TOKEN??????????")

    prompt = load_scoring_prompt()
    note_payload = {
        "title": note.get("title") or "",
        "body": note.get("body") or "",
        "like_count": int(note.get("like_count") or 0),
        "collect_count": int(note.get("collect_count") or 0),
        "comment_count": int(note.get("comment_count") or 0),
        "share_count": int(note.get("share_count") or 0),
        "cover_url": cover_url or "",
    }
    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"{prompt}\n\n????????\n{json.dumps(note_payload, ensure_ascii=False)}"}
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
    proxy = os.getenv("KIE_PROXY", "http://127.0.0.1:7890").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    response = requests.post(KIE_URL, json=payload, headers=headers, proxies=proxies, stream=True, timeout=120)
    response.raise_for_status()

    result = _extract_json(_stream_content(response))
    result["????"] = calculate_burst_signal(
        result,
        note_payload["like_count"],
        note_payload["collect_count"],
        note_payload["comment_count"],
    )
    return result
