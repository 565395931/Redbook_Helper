from __future__ import annotations

import json
import os
import sys
import warnings
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Any

import requests
import urllib3

ROOT = Path(__file__).resolve().parents[2]
SPIDER_ROOT = ROOT / "vendor" / "Spider_XHS"


def _load_spider_modules() -> None:
    spider_path = str(SPIDER_ROOT)
    if spider_path not in sys.path:
        sys.path.insert(0, spider_path)


@contextmanager
def _spider_runtime():
    _load_spider_modules()
    previous_cwd = Path.cwd()
    os.chdir(SPIDER_ROOT)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
            yield
    finally:
        os.chdir(previous_cwd)


def _parse_note_url(note_url: str) -> tuple[str, str, str]:
    parsed = urlparse(note_url)
    note_id = parsed.path.rstrip("/").split("/")[-1]
    query = parse_qs(parsed.query)
    xsec_token = query.get("xsec_token", [""])[0]
    xsec_source = query.get("xsec_source", ["pc_search"])[0]
    return note_id, xsec_token, xsec_source


def _resolve_share_url(note_url: str) -> str:
    parsed = urlparse(note_url)
    if parsed.netloc.endswith("xhslink.com"):
        try:
            response = requests.get(note_url, allow_redirects=True, timeout=8)
            return response.url
        except requests.RequestException:
            return note_url
    return note_url


def _note_url(note_id: str, xsec_token: str, xsec_source: str = "pc_search") -> str:
    return f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source={xsec_source}"


def _parse_detail(detail: dict[str, Any], note_url: str) -> dict[str, Any] | None:
    from xhs_utils.data_util import handle_note_info

    try:
        raw_item = detail["data"]["items"][0]
        raw_item["url"] = note_url
        parsed = handle_note_info(raw_item)
        parsed["raw_json"] = json.dumps(raw_item, ensure_ascii=False)
        return parsed
    except Exception:
        return None


def _count(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if value.endswith("万"):
            try:
                return int(float(value[:-1]) * 10000)
            except ValueError:
                return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _image_urls(note_card: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for image in note_card.get("image_list") or []:
        for info in image.get("info_list") or []:
            url = info.get("url")
            if url:
                urls.append(url)
                break
    cover = note_card.get("cover") or {}
    cover_url = cover.get("url_default") or cover.get("url_pre")
    if cover_url and cover_url not in urls:
        urls.insert(0, cover_url)
    return urls


def _parse_result_item(item: dict[str, Any], xsec_source: str = "pc_search") -> dict[str, Any] | None:
    note_id = item.get("note_id") or item.get("id")
    note_card = item.get("note_card") or item
    xsec_token = item.get("xsec_token") or item.get("xsecToken") or note_card.get("xsec_token") or ""
    if not note_id:
        return None

    user = note_card.get("user") or {}
    interact = note_card.get("interact_info") or {}
    note_type = note_card.get("type") or item.get("model_type") or ""
    title = note_card.get("title") or note_card.get("display_title") or ""
    desc = note_card.get("desc") or note_card.get("display_desc") or ""
    tags = []
    for tag in note_card.get("tag_list") or []:
        name = tag.get("name")
        if name:
            tags.append(name)

    note_url = _note_url(note_id, xsec_token, xsec_source) if xsec_token else f"https://www.xiaohongshu.com/explore/{note_id}"
    parsed = {
        "note_id": note_id,
        "note_url": note_url,
        "note_type": "视频" if note_type == "video" else "图集",
        "user_id": user.get("user_id", ""),
        "home_url": f"https://www.xiaohongshu.com/user/profile/{user.get('user_id', '')}" if user.get("user_id") else "",
        "nickname": user.get("nickname") or user.get("nick_name") or "",
        "avatar": user.get("avatar") or "",
        "title": title or "无标题",
        "desc": desc,
        "liked_count": _count(interact.get("liked_count")),
        "collected_count": _count(interact.get("collected_count")),
        "comment_count": _count(interact.get("comment_count")),
        "share_count": _count(interact.get("share_count") or interact.get("shared_count")),
        "video_cover": (note_card.get("cover") or {}).get("url_default") or (note_card.get("cover") or {}).get("url_pre"),
        "video_addr": None,
        "image_list": _image_urls(note_card),
        "tags": tags,
        "upload_time": _timestamp_text(note_card.get("time") or note_card.get("create_time")),
        "ip_location": "",
        "raw_json": json.dumps(item, ensure_ascii=False),
    }
    return parsed


def _parse_web_initial_state(html: str) -> dict[str, Any] | None:
    import re

    match = re.search(r"window\.__INITIAL_STATE__=(.*?)</script>", html, re.S)
    if not match:
        return None
    state_text = match.group(1).replace("undefined", "null")
    try:
        return json.loads(state_text)
    except json.JSONDecodeError:
        return None


def _timestamp_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    from datetime import datetime

    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def fetch_web_note_detail(note_url: str, cookie: str) -> dict[str, Any] | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Referer": "https://www.xiaohongshu.com/",
        "Cookie": cookie,
    }
    try:
        response = requests.get(note_url, headers=headers, timeout=20)
    except requests.RequestException:
        return None
    if response.status_code >= 400:
        return None

    state = _parse_web_initial_state(response.text)
    detail_map = (((state or {}).get("note") or {}).get("noteDetailMap") or {})
    if not detail_map:
        return None

    parsed_url = urlparse(note_url)
    note_id = parsed_url.path.rstrip("/").split("/")[-1]
    entry = detail_map.get(note_id) or next(iter(detail_map.values()))
    note = (entry or {}).get("note") or {}
    if not note:
        return None

    interact = note.get("interactInfo") or {}
    images = []
    for image in note.get("imageList") or []:
        url = image.get("urlDefault") or image.get("urlPre") or image.get("url")
        if not url:
            for info in image.get("infoList") or []:
                url = info.get("url")
                if url:
                    break
        if url:
            images.append(url)
    tags = [tag.get("name") for tag in note.get("tagList") or [] if tag.get("name")]
    user = note.get("user") or {}
    xsec_token = note.get("xsecToken") or _parse_note_url(note_url)[1]

    return {
        "note_id": note.get("noteId") or note_id,
        "note_url": _note_url(note.get("noteId") or note_id, xsec_token, _parse_note_url(note_url)[2]) if xsec_token else note_url,
        "note_type": "视频" if note.get("type") == "video" else "图集",
        "user_id": user.get("userId") or user.get("user_id") or "",
        "home_url": f"https://www.xiaohongshu.com/user/profile/{user.get('userId') or user.get('user_id')}" if (user.get("userId") or user.get("user_id")) else "",
        "nickname": user.get("nickname") or user.get("nickName") or "",
        "avatar": user.get("avatar") or "",
        "title": note.get("title") or "无标题",
        "desc": note.get("desc") or "",
        "liked_count": _count(interact.get("likedCount") or interact.get("liked_count")),
        "collected_count": _count(interact.get("collectedCount") or interact.get("collected_count")),
        "comment_count": _count(interact.get("commentCount") or interact.get("comment_count")),
        "share_count": _count(interact.get("shareCount") or interact.get("share_count")),
        "video_cover": images[0] if images else None,
        "video_addr": None,
        "image_list": images,
        "tags": tags,
        "upload_time": _timestamp_text(note.get("time") or note.get("createTime") or note.get("create_time")),
        "ip_location": note.get("ipLocation") or "",
        "raw_json": json.dumps(note, ensure_ascii=False),
    }


def _merge_note(primary: dict[str, Any] | None, fallback: dict[str, Any] | None) -> dict[str, Any] | None:
    if not primary:
        return fallback
    if not fallback:
        return primary
    merged = dict(primary)
    for key, value in fallback.items():
        if key == "raw_json":
            continue
        if value and (not merged.get(key) or merged.get(key) in ([], 0)):
            merged[key] = value
    return merged


def crawl_note_url(note_url: str, cookie: str) -> list[dict[str, Any]]:
    with _spider_runtime():
        from apis.xhs_pc_apis import XHS_Apis

        api = XHS_Apis()
        note_url = _resolve_share_url(note_url)
        note_id, xsec_token, xsec_source = _parse_note_url(note_url)
        if not note_id or not xsec_token:
            raise RuntimeError("单条笔记链接需要包含 xsec_token 参数，请复制浏览器地址栏里的完整小红书链接。")
        normalized_url = _note_url(note_id, xsec_token, xsec_source)
        detail_success, detail_msg, detail = api.get_note_info(normalized_url, cookie)
        parsed = None
        if not detail_success:
            parsed = fetch_web_note_detail(normalized_url, cookie)
        else:
            parsed = _parse_detail(detail, normalized_url)
            if not parsed or not parsed.get("desc"):
                parsed = _merge_note(parsed, fetch_web_note_detail(normalized_url, cookie))
        return [parsed] if parsed else []


def _item_like_count(item: dict[str, Any]) -> int:
    note_card = item.get("note_card") or item
    interact = note_card.get("interact_info") or {}
    return _count(interact.get("liked_count"))


def crawl_user_notes(profile_url: str, cookie: str, limit: int = 20, sort_mode: str = "latest") -> list[dict[str, Any]]:
    with _spider_runtime():
        from apis.xhs_pc_apis import XHS_Apis

        api = XHS_Apis()
        success, msg, simple_notes = api.get_user_all_notes(profile_url, cookie)
        if not success:
            raise RuntimeError(str(msg))

        if sort_mode == "likes":
            simple_notes = sorted(simple_notes, key=_item_like_count, reverse=True)

        results: list[dict[str, Any]] = []
        for item in simple_notes[:limit]:
            note_id = item.get("note_id") or item.get("id")
            xsec_token = item.get("xsec_token", "")
            if not note_id:
                continue
            note_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source=pc_user"
            detail_success, detail_msg, detail = api.get_note_info(note_url, cookie)
            parsed = None
            if detail_success:
                parsed = _parse_detail(detail, note_url)
            if not parsed:
                parsed = _parse_result_item(item, "pc_user")
            if parsed and not parsed.get("desc"):
                parsed = _merge_note(parsed, fetch_web_note_detail(note_url, cookie))
            if parsed:
                results.append(parsed)
        return results


def crawl_keyword_notes(keyword: str, cookie: str, limit: int = 20, sort_type_choice: int = 0) -> list[dict[str, Any]]:
    with _spider_runtime():
        from apis.xhs_pc_apis import XHS_Apis

        api = XHS_Apis()
        success, msg, simple_notes = api.search_some_note(
            keyword,
            limit,
            cookie,
            sort_type_choice=sort_type_choice,
            note_type=2,
        )
        if not success:
            raise RuntimeError(str(msg))

        results: list[dict[str, Any]] = []
        for item in simple_notes[:limit]:
            note_id = item.get("note_id") or item.get("id")
            xsec_token = item.get("xsec_token", "")
            if not note_id:
                note_card = item.get("note_card") or {}
                note_id = note_card.get("note_id") or note_card.get("id")
            if not xsec_token:
                xsec_token = item.get("xsecToken") or item.get("xsec_token") or ""
            if not note_id or not xsec_token:
                continue
            note_url = _note_url(note_id, xsec_token, "pc_search")
            detail_success, detail_msg, detail = api.get_note_info(note_url, cookie)
            parsed = None
            if detail_success:
                parsed = _parse_detail(detail, note_url)
            if not parsed:
                parsed = _parse_result_item(item, "pc_search")
            if parsed and not parsed.get("desc"):
                parsed = _merge_note(parsed, fetch_web_note_detail(note_url, cookie))
            if parsed:
                results.append(parsed)
        return results


def detect_target_type(target: str) -> str:
    value = target.strip()
    if "xiaohongshu.com/explore/" in value or "xiaohongshu.com/discovery/item/" in value:
        return "note"
    if "xhslink.com/" in value:
        return "note"
    if "xiaohongshu.com/user/profile/" in value:
        return "profile"
    if value.startswith(("http://", "https://")):
        return "note"
    return "keyword"


def crawl_target(
    target: str,
    cookie: str,
    limit: int = 20,
    keyword_sort: int = 0,
) -> tuple[str, list[dict[str, Any]]]:
    target_type = detect_target_type(target)
    if target_type == "note":
        return target_type, crawl_note_url(target, cookie)
    if target_type == "profile":
        return target_type, crawl_user_notes(target, cookie, limit=limit)
    return target_type, crawl_keyword_notes(target, cookie, limit=limit, sort_type_choice=keyword_sort)


def fetch_self_published(cookie: str) -> list[dict[str, Any]]:
    with _spider_runtime():
        from apis.xhs_creator_apis import XHS_Creator_Apis

        api = XHS_Creator_Apis()
        success, msg, notes = api.get_all_publish_note_info(cookie)
        if not success:
            raise RuntimeError(str(msg))
        return notes
