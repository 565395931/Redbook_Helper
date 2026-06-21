from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
GENERATE_PROMPT_PATH = ROOT_DIR / "prompts" / "generate_prompt.md"
REMIX_PROMPT_PATH = ROOT_DIR / "prompts" / "copywriting_prompt.md"
DEBUG_DIR = ROOT_DIR / "app" / "data" / "debug" / "copywriting"
MAX_RETRIES = 3
DIFFERENTIATION_LABELS = {
    "light": "轻度差异化：保留相似框架",
    "medium": "中度差异化：保留爆款逻辑，表达明显不同",
    "high": "高度差异化：只保留底层策略，整体重新创作",
}


def load_prompt(generation_mode: str = "normal") -> str:
    prompt_path = REMIX_PROMPT_PATH if generation_mode == "remix" else GENERATE_PROMPT_PATH
    return prompt_path.read_text(encoding="utf-8").strip()


def _write_debug_log(record: dict[str, Any]) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    request_id = str(record.get("request_id") or "unknown")
    attempt = int(record.get("attempt") or 0)
    log_path = DEBUG_DIR / f"{stamp}-{request_id}-attempt{attempt}.json"
    log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def log_copywriting_debug(record: dict[str, Any]) -> None:
    _write_debug_log(record)


def build_copywriting_payload(brand_profile: dict | None, form_data: dict[str, str]) -> str:
    brand_profile = brand_profile or {}
    word_count = str(form_data.get("word_count") or "").strip()
    generation_mode = str(form_data.get("generation_mode") or "normal").strip()
    differentiation_level = str(form_data.get("differentiation_level") or "").strip()
    reference_title = str(form_data.get("reference_title") or "").strip()
    reference_content = str(form_data.get("reference_content") or "").strip()
    word_count_instruction = ""
    if word_count and word_count.isdigit():
        word_count_instruction = f"\n正文目标字数：\n请将正文控制在 {word_count} 字左右\n"
    reference_block = ""
    if generation_mode == "remix" and (reference_title or reference_content):
        reference_block = (
            "\n参考爆款文案：\n"
            f"标题：\n{reference_title or '未提供'}\n\n"
            f"正文：\n{reference_content or '未提供'}\n\n"
            f"差异化程度：\n{DIFFERENTIATION_LABELS.get(differentiation_level, DIFFERENTIATION_LABELS['medium'])}\n"
        )
    return (
        "账号定位：\n"
        f"账号主题：\n{(brand_profile.get('main_theme') or '').strip() or '未填写'}\n\n"
        f"目标人群：\n{(brand_profile.get('audience') or '').strip() or '未填写'}\n\n"
        f"表达风格：\n{(brand_profile.get('tone') or '').strip() or '未填写'}\n\n"
        f"产品特点：\n{(brand_profile.get('product_points') or '').strip() or '未填写'}\n\n"
        "本次生成信息：\n"
        f"本次主题：\n{form_data['post_topic']}\n\n"
        f"笔记类型：\n{form_data['post_type']}\n\n"
        f"发布目标：\n{form_data['post_goal']}\n\n"
        f"核心观点：\n{form_data['core_message'] or '未填写'}\n"
        f"{reference_block}"
        f"{word_count_instruction}"
    )


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _clean_title(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"^\s*(?:[-•*]|\d+[.、])\s*", "", text).strip()


def _split_tag_string(raw_tags: str) -> list[str]:
    cleaned = (
        str(raw_tags)
        .replace("，", ",")
        .replace("、", ",")
        .replace("；", ",")
        .replace(";", ",")
    )
    parts = re.split(r"[\n,#]+", cleaned)
    return [part.strip(" \t\r\n-·•#") for part in parts if part.strip(" \t\r\n-·•#")]


def _strip_trailing_hashtag_block(body: str) -> str:
    text = str(body or "").strip()
    if not text:
        return ""
    lines = [line.rstrip() for line in text.splitlines()]
    while lines:
        current = lines[-1].strip()
        if not current:
            lines.pop()
            continue
        hashtag_parts = re.findall(r"#([^\s#]+)", current)
        normalized = re.sub(r"#([^\s#]+)", "", current).strip()
        if hashtag_parts and not normalized:
            lines.pop()
            continue
        break

    collapsed = "\n".join(lines).strip()
    collapsed = re.sub(r"(?:\s*#([^\s#]+)){4,}\s*$", "", collapsed).strip()
    return collapsed


def build_fallback_tags(brand_profile: dict | None, form_data: dict[str, str], body: str) -> list[str]:
    brand_profile = brand_profile or {}
    candidates = [
        form_data.get("post_type", "").strip(),
        form_data.get("post_goal", "").strip(),
        (brand_profile.get("main_theme") or "").strip(),
        (brand_profile.get("audience") or "").strip(),
    ]

    topic = form_data.get("post_topic", "").strip()
    if topic:
        candidates.extend(part.strip() for part in re.split(r"[、，,/\s]+", topic) if 1 < len(part.strip()) <= 12)

    if body:
        candidates.extend(re.findall(r"[一-龥A-Za-z0-9]{2,12}", body)[:8])

    cleaned: list[str] = []
    for item in candidates:
        value = str(item).strip().lstrip("#")
        if not value or value in {"未填写", "正文", "标题"}:
            continue
        cleaned.append(value)
    return _dedupe_keep_order(cleaned)


def normalize_copywriting_result(
    payload: Any,
    *,
    brand_profile: dict | None,
    form_data: dict[str, str],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("模型返回格式不正确，请重试。")

    raw_titles = (
        payload.get("tittle")
        or payload.get("title")
        or payload.get("titles")
        or payload.get("title_options")
        or payload.get("标题")
        or []
    )
    if isinstance(raw_titles, str):
        titles = [_clean_title(item) for item in raw_titles.splitlines() if item.strip()]
    else:
        titles = [_clean_title(item) for item in raw_titles if str(item).strip()]
    titles = _dedupe_keep_order([title for title in titles if title])[:3]

    body = _strip_trailing_hashtag_block(str(
        payload.get("content")
        or payload.get("body")
        or payload.get("正文")
        or ""
    ).strip())

    raw_tags = (
        payload.get("recommend")
        or payload.get("tags")
        or payload.get("hashtags")
        or payload.get("推荐标签")
        or payload.get("标签")
        or []
    )
    if isinstance(raw_tags, str):
        tags = _split_tag_string(raw_tags)
    else:
        tags = [str(item).strip().lstrip("#") for item in raw_tags if str(item).strip()]
    tags = _dedupe_keep_order([tag for tag in tags if tag])

    if len(tags) < 5:
        tags.extend(build_fallback_tags(brand_profile, form_data, body))
        tags = _dedupe_keep_order([tag.strip().lstrip("#") for tag in tags if tag.strip()])[:10]

    if len(titles) < 3:
        raise ValueError("模型没有返回 3 个标题，请重试。")
    if not body:
        raise ValueError("模型没有返回正文，请重试。")
    if len(tags) < 5:
        raise ValueError("模型返回的标签少于 5 个，请重试。")

    return {"titles": titles, "body": body, "tags": tags[:10]}


def parse_model_json_content(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\s*```$", "", text).strip()

    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and start < end:
        candidates.append(text[start : end + 1])
    if start != -1:
        partial = text[start:].strip()
        candidates.append(re.sub(r",\s*$", "", partial) + "}")

    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    for candidate in dict.fromkeys(item for item in candidates if item):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            try:
                parsed, _ = decoder.raw_decode(candidate)
            except json.JSONDecodeError as raw_exc:
                last_error = raw_exc
                continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("DeepSeek 返回的 JSON 无法解析，请重试。") from last_error


def generate_copywriting(
    *,
    api_key: str,
    brand_profile: dict | None,
    form_data: dict[str, str],
    model: str = "deepseek-v4-flash",
) -> dict[str, Any]:
    if not api_key.strip():
        raise ValueError("请先在设置页填写 DeepSeek API Key。")

    prompt = load_prompt(str(form_data.get("generation_mode") or "normal").strip())
    user_content = build_copywriting_payload(brand_profile, form_data)
    request_json = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ],
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": 1800,
        "stream": False,
    }
    request_id = uuid4().hex[:12]
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        debug_record: dict[str, Any] = {
            "request_id": request_id,
            "attempt": attempt,
            "endpoint": "https://api.deepseek.com/chat/completions",
            "request": request_json,
        }
        response: requests.Response | None = None
        try:
            response = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key.strip()}",
                    "Content-Type": "application/json",
                },
                json=request_json,
                timeout=90,
            )
            debug_record["response_status"] = response.status_code
            debug_record["response_text"] = response.text
            response.raise_for_status()

            payload = response.json()
            debug_record["response_json"] = payload
            content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
            debug_record["model_content"] = content
            if not content:
                raise ValueError("DeepSeek 没有返回内容，请稍后再试。")
            parsed = parse_model_json_content(content)
            debug_record["parsed_content"] = parsed
            result = normalize_copywriting_result(parsed, brand_profile=brand_profile, form_data=form_data)
            debug_record["normalized_result"] = result
            _write_debug_log(debug_record)
            return result
        except Exception as exc:
            last_error = exc
            debug_record["error_type"] = type(exc).__name__
            debug_record["error_message"] = str(exc)
            if response is not None and "response_status" not in debug_record:
                debug_record["response_status"] = response.status_code
                debug_record["response_text"] = response.text
            _write_debug_log(debug_record)

    if last_error is not None:
        raise last_error
    raise ValueError("文案生成失败，请重试。")
