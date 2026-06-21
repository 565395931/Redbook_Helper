from __future__ import annotations

import base64
import html
import itertools
import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from threading import RLock
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote
from urllib.parse import quote as url_quote
from urllib.parse import urlparse
from urllib.parse import unquote

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
import requests

from app.db import as_json, connect, from_json, migrate
from app.services.ai_scoring import DEFAULT_KIE_PROXY, normalize_score_result, score_note_with_model
from app.services.copywriting import generate_copywriting, log_copywriting_debug
from app.services.crawler import crawl_target, crawl_user_notes, fetch_self_published
from app.services.dedupe import exact_duplicate_segments

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "data" / "media"
DAILY_CRAWL_LIMIT = 25
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_TASK_COUNTER = itertools.count(1)
IMAGE_RUNTIME_TASKS: dict[int, list[dict[str, object]]] = {}
IMAGE_TASK_LOCK = RLock()
VIDEO_MIGRATION_TASK_COUNTER = itertools.count(1)
VIDEO_MIGRATION_RUNTIME_TASKS: dict[int, list[dict[str, object]]] = {}
VIDEO_MIGRATION_DISPLAY_COUNTERS: dict[int, int] = {}
VIDEO_MIGRATION_TASK_LOCK = RLock()
RUNNINGHUB_WEBAPP_ID = "2067898441841336321"
RUNNINGHUB_BASE_URL = "https://www.runninghub.cn"
RUNNINGHUB_MAX_UPLOAD_BYTES = 30 * 1024 * 1024
RUNNINGHUB_VIDEO_POLL_INTERVAL = 30

app = FastAPI(title="Redbook Analisyze")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

INVISIBLE_PASTE_CHARS = {
    "\ufeff": "",
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
    "\u2060": "",
    "\xa0": " ",
}


def clean_pasted_text(value: str | None) -> str:
    text = str(value or "")
    for source, target in INVISIBLE_PASTE_CHARS.items():
        text = text.replace(source, target)
    return text.strip()


def render_topics(value: str | None) -> Markup:
    text = str(value or "")
    pieces: list[str] = []
    last_end = 0
    pattern = re.compile(r"#([^#\r\n]+?)\[璇濋\]#")
    for match in pattern.finditer(text):
        pieces.append(str(escape(text[last_end : match.start()])))
        topic = str(escape(match.group(1).strip()))
        pieces.append(f'<span class="topic-tag">{topic}</span>')
        last_end = match.end()
    pieces.append(str(escape(text[last_end:])))
    html = "".join(pieces).replace("#", "").replace("[璇濋]", "")
    return Markup(html)


templates.env.filters["render_topics"] = render_topics

TOPIC_PATTERN = re.compile(r"#([^#\r\n\[]+?)(?:\[话题\])?#")
COPYWRITING_TYPES = [
    "干货教程",
    "经验分享",
    "避坑指南",
    "案例拆解",
    "产品种草",
    "清单合集",
    "故事经历",
    "观点输出",
    "测评对比",
    "热点借势",
]
COPYWRITING_GOALS = [
    "获得点赞",
    "获得收藏",
    "获得评论",
    "获得私信咨询",
    "获得客户转化",
    "品牌曝光",
]
COPYWRITING_WORD_COUNTS = ["默认", "100", "200", "300", "400", "500"]
COPYWRITING_DIFFERENTIATION_LEVELS = {
    "light": "轻度差异化：保留相似框架",
    "medium": "中度差异化：保留爆款逻辑，表达明显不同",
    "high": "高度差异化：只保留底层策略，整体重新创作",
}
IMAGE_OUTPUT_PROMPTS = {
    "小红书封面": """生成一张高点击率的小红书封面图。

画面主体突出，视觉焦点明确，符合年轻用户审美；整体风格精致、高级、有质感；采用热门社交媒体视觉设计，构图简洁，层次分明；主体占画面主要区域，背景干净不杂乱；色彩明亮且具有吸引力；具备强烈的种草感和分享欲；适合移动端浏览；高清细节，商业级视觉质量，真实自然光影。""",
    "商品主图": """生成专业电商商品主图。

商品主体居中且完整展示，占据主要视觉区域；背景简洁干净，突出商品卖点；商业广告摄影风格；光线均匀，高级布光；细节清晰锐利；材质表现真实；画面具有高级感和购买吸引力；符合品牌宣传视觉标准；超高清，高分辨率，商业级产品摄影效果。""",
    "详情页主图": """生成电商详情页主视觉。

突出产品核心卖点与使用场景；画面具有商业广告大片质感；产品主体清晰，重点突出；场景设计真实且富有氛围感；光影高级，层次丰富；构图适合电商详情页展示；体现产品价值感、品质感和专业感；增强用户购买欲；高清细节，商业级广告视觉，高品质渲染效果。""",
}


def build_image_generation_prompt(output_target: str, user_prompt: str) -> str:
    cleaned_user_prompt = user_prompt.strip()
    style_prompt = IMAGE_OUTPUT_PROMPTS.get(output_target.strip(), "").strip()
    if not style_prompt:
        return cleaned_user_prompt
    return f"【风格模板】\n\n{style_prompt}\n\n【用户需求】\n\n{cleaned_user_prompt}"


def default_copywriting_form() -> dict[str, str]:
    return {
        "post_topic": "",
        "post_type": COPYWRITING_TYPES[1],
        "post_goal": COPYWRITING_GOALS[1],
        "word_count": COPYWRITING_WORD_COUNTS[0],
        "core_message": "",
        "source_note_id": "",
        "generation_mode": "normal",
        "differentiation_level": "medium",
        "reference_title": "",
        "reference_content": "",
    }


def mask_secret(value: str, prefix: int = 4, suffix: int = 4) -> str:
    secret = str(value or "").strip()
    if not secret:
        return ""
    if len(secret) <= prefix + suffix:
        return secret[:1] + "*" * max(0, len(secret) - 2) + secret[-1:]
    return f"{secret[:prefix]}{'*' * 8}{secret[-suffix:]}"


def extract_note_keywords(body: str | None, tags: list[str]) -> list[str]:
    values = [match.group(1).strip() for match in TOPIC_PATTERN.finditer(str(body or ""))]
    values.extend(str(tag).strip() for tag in tags if str(tag).strip())
    return list(dict.fromkeys(value for value in values if value))


def strip_note_topics(body: str | None) -> str:
    return re.sub(r"\s{2,}", " ", TOPIC_PATTERN.sub("", str(body or ""))).strip()


def publication_age_days(published_at: str | None) -> int | None:
    if not published_at:
        return None
    try:
        published = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        now = datetime.now(published.tzinfo) if published.tzinfo else datetime.now()
        return max(0, (now - published).days)
    except (TypeError, ValueError):
        return None


@app.on_event("startup")
def startup() -> None:
    migrate()


def redirect_home() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


def redirect_page(page: str) -> RedirectResponse:
    return RedirectResponse(f"/?page={page}", status_code=303)


def redirect_page_with_error(page: str, message: str, query_key: str = "crawl_error") -> RedirectResponse:
    return RedirectResponse(f"/?page={page}&{query_key}={quote(message[:240])}", status_code=303)


def redirect_page_with_notice(page: str, message: str, query_key: str = "crawl_notice") -> RedirectResponse:
    return RedirectResponse(f"/?page={page}&{query_key}={quote(message[:240])}", status_code=303)


def build_copywriting_prefill(request: Request, latest_generation: dict | None = None) -> dict[str, str]:
    form_data = default_copywriting_form()
    latest_generation = latest_generation or {}
    if latest_generation:
        form_data.update(
            {
                "post_topic": str(latest_generation.get("post_topic") or "").strip(),
                "post_type": str(latest_generation.get("post_type") or "").strip() or form_data["post_type"],
                "post_goal": str(latest_generation.get("post_goal") or "").strip() or form_data["post_goal"],
                "word_count": str(latest_generation.get("word_count") or "").strip() or form_data["word_count"],
                "core_message": str(latest_generation.get("core_message") or "").strip(),
                "source_note_id": str(latest_generation.get("source_note_id") or "").strip(),
                "generation_mode": str(latest_generation.get("generation_mode") or "").strip() or form_data["generation_mode"],
                "differentiation_level": str(latest_generation.get("differentiation_level") or "").strip() or form_data["differentiation_level"],
                "reference_title": str(latest_generation.get("reference_title") or "").strip(),
                "reference_content": str(latest_generation.get("reference_content") or "").strip(),
            }
        )

    for key in form_data:
        query_value = str(request.query_params.get(key, "")).strip()
        if query_value:
            form_data[key] = query_value

    if form_data["post_type"] not in COPYWRITING_TYPES:
        form_data["post_type"] = COPYWRITING_TYPES[1]
    if form_data["post_goal"] not in COPYWRITING_GOALS:
        form_data["post_goal"] = COPYWRITING_GOALS[1]
    if form_data["word_count"] not in COPYWRITING_WORD_COUNTS:
        form_data["word_count"] = COPYWRITING_WORD_COUNTS[0]
    if form_data["generation_mode"] not in {"normal", "remix"}:
        form_data["generation_mode"] = "normal"
    if form_data["differentiation_level"] not in COPYWRITING_DIFFERENTIATION_LEVELS:
        form_data["differentiation_level"] = "medium"
    return form_data


def get_current_account() -> dict | None:
    with connect() as conn:
        account = conn.execute("SELECT * FROM accounts WHERE is_current = 1 LIMIT 1").fetchone()
        if account:
            return account
        account = conn.execute("SELECT * FROM accounts ORDER BY id LIMIT 1").fetchone()
        if account:
            conn.execute("UPDATE accounts SET is_current = CASE WHEN id = ? THEN 1 ELSE 0 END", (account["id"],))
        return account


def get_app_setting(key: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]).strip() if row else ""


def get_runninghub_api_key() -> str:
    return get_app_setting("runninghub_api_key") or str(os.getenv("RUNNINGHUB_API_KEY") or "").strip()


def normalize_proxy_url(proxy: str) -> str:
    value = str(proxy or "").strip()
    if not value:
        return ""
    if "://" not in value:
        return f"http://{value}"
    return value


def get_kie_proxy_value() -> str:
    return get_app_setting("kie_proxy") or DEFAULT_KIE_PROXY


def get_kie_proxies() -> dict[str, str] | None:
    proxy_url = normalize_proxy_url(get_kie_proxy_value())
    return {"http": proxy_url, "https": proxy_url} if proxy_url else None


def today_key() -> str:
    return date.today().isoformat()


def get_daily_crawl_used(account_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT used_count FROM crawl_usage WHERE account_id = ? AND usage_date = ?",
            (account_id, today_key()),
        ).fetchone()
    return int(row["used_count"]) if row else 0


def get_daily_crawl_remaining(account_id: int) -> int:
    return max(0, DAILY_CRAWL_LIMIT - get_daily_crawl_used(account_id))


def add_daily_crawl_usage(account_id: int, count: int) -> None:
    if count <= 0:
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO crawl_usage(account_id, usage_date, used_count)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id, usage_date) DO UPDATE SET
                used_count = MIN(?, used_count + excluded.used_count),
                updated_at = CURRENT_TIMESTAMP
            """,
            (account_id, today_key(), count, DAILY_CRAWL_LIMIT),
        )


def mark_account_expired(account_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE accounts SET login_status = 'expired', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (account_id,),
        )


def _note_identity(note: dict) -> tuple[str | None, str | None]:
    note_id = str(note.get("note_id") or "").strip() or None
    note_url = str(note.get("note_url") or "").strip() or None
    return note_id, note_url


def _note_exists(conn, account_id: int, note_id: str | None, note_url: str | None) -> bool:
    if note_id:
        row = conn.execute(
            "SELECT id FROM notes WHERE account_id = ? AND platform_note_id = ? LIMIT 1",
            (account_id, note_id),
        ).fetchone()
        if row:
            return True
    if note_url:
        row = conn.execute(
            "SELECT id FROM notes WHERE account_id = ? AND note_url = ? LIMIT 1",
            (account_id, note_url),
        ).fetchone()
        if row:
            return True
    return False


def _safe_image_suffix(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"
    return ".jpg"


def save_reference_upload(account_id: int, upload: UploadFile) -> str | None:
    if not upload.filename:
        return None
    suffix = _safe_image_suffix(upload.filename, upload.content_type or "")
    upload.file.seek(0)
    return save_reference_bytes(account_id, upload.filename, upload.content_type or "", upload.file.read())


def save_reference_bytes(account_id: int, filename: str, content_type: str, content: bytes) -> str | None:
    if not filename or not content:
        return None
    suffix = _safe_image_suffix(filename, content_type)
    relative_path = Path("image_references") / str(account_id) / f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    absolute_path = MEDIA_DIR / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    with absolute_path.open("wb") as target:
        target.write(content)
    return "/media/" + relative_path.as_posix()


def upload_reference_to_kie(api_key: str, upload: UploadFile, account_id: int) -> str:
    if not upload.filename:
        raise RuntimeError("参考图文件名为空")
    suffix = _safe_image_suffix(upload.filename, upload.content_type or "")
    file_name = f"reference-{account_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    upload.file.seek(0)
    return upload_reference_bytes_to_kie(
        api_key,
        upload.file.read(),
        file_name,
        upload.content_type or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream",
        account_id,
    )


def upload_reference_bytes_to_kie(api_key: str, content: bytes, file_name: str, content_type: str, account_id: int) -> str:
    response = requests.post(
        "https://kieai.redpandaai.co/api/file-stream-upload",
        headers={"Authorization": f"Bearer {api_key}"},
        data={
            "uploadPath": f"images/redbook-helper/{account_id}",
            "fileName": file_name,
        },
        files={
            "file": (
                file_name,
                content,
                content_type or "application/octet-stream",
            )
        },
        proxies=get_kie_proxies(),
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    file_url = str(data.get("fileUrl") or data.get("downloadUrl") or "").strip()
    if not file_url:
        message = str(payload.get("msg") or "KIE 文件上传成功但未返回图片链接")
        raise RuntimeError(message[:300])
    return file_url


def upload_reference_data_url_to_kie(api_key: str, data_url: str, account_id: int) -> str:
    match = re.match(r"^data:(?P<mime>image/[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$", data_url, re.DOTALL)
    if not match:
        raise RuntimeError("参考图 data URL 格式无效")
    mime_type = match.group("mime")
    suffix = mimetypes.guess_extension(mime_type) or ".jpg"
    content = base64.b64decode(match.group("data"))
    file_name = f"reference-{account_id}-{uuid.uuid4().hex}{suffix}"
    return upload_reference_bytes_to_kie(api_key, content, file_name, mime_type, account_id)


def upload_reference_media_url_to_kie(api_key: str, media_url: str, account_id: int) -> str:
    parsed_path = unquote(urlparse(media_url).path)
    if not parsed_path.startswith("/media/"):
        raise RuntimeError("参考图不是本地媒体地址")
    relative_path = parsed_path.removeprefix("/media/").lstrip("/")
    absolute_path = (MEDIA_DIR / relative_path).resolve()
    media_root = MEDIA_DIR.resolve()
    if absolute_path == media_root or media_root not in absolute_path.parents or not absolute_path.is_file():
        raise RuntimeError("本地参考图不存在")
    mime_type = mimetypes.guess_type(absolute_path.name)[0] or "image/jpeg"
    file_name = f"reference-{account_id}-{uuid.uuid4().hex}{absolute_path.suffix or '.jpg'}"
    return upload_reference_bytes_to_kie(api_key, absolute_path.read_bytes(), file_name, mime_type, account_id)


def runninghub_endpoint(path: str) -> str:
    return f"{RUNNINGHUB_BASE_URL.rstrip('/')}/{path.lstrip('/')}"


def runninghub_post(api_key: str, path: str, **kwargs) -> dict[str, object]:
    headers = kwargs.pop("headers", {}) or {}
    if "files" not in kwargs:
        headers.setdefault("Content-Type", "application/json")
    response = requests.post(
        runninghub_endpoint(path),
        headers=headers,
        data=kwargs.pop("data", None),
        json=kwargs.pop("json", None),
        files=kwargs.pop("files", None),
        timeout=kwargs.pop("timeout", 60),
    )
    response.raise_for_status()
    payload = response.json()
    code = str(payload.get("code") or "").strip()
    if code and code not in {"0", "200"}:
        message = str(payload.get("msg") or payload.get("message") or "RunningHub 请求失败")
        raise RuntimeError(message[:300])
    return payload


def runninghub_get(api_key: str, path: str, **kwargs) -> dict[str, object]:
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("Authorization", f"Bearer {api_key}")
    response = requests.get(
        runninghub_endpoint(path),
        params=kwargs.pop("params", None),
        headers=headers,
        timeout=kwargs.pop("timeout", 60),
    )
    response.raise_for_status()
    payload = response.json()
    code = str(payload.get("code") or "").strip()
    if code and code not in {"0", "200"}:
        message = str(payload.get("msg") or payload.get("message") or "RunningHub 请求失败")
        raise RuntimeError(message[:300])
    return payload


def upload_bytes_to_runninghub(api_key: str, content: bytes, file_name: str, content_type: str, file_type: str) -> str:
    response = requests.post(
        runninghub_endpoint("/openapi/v2/media/upload/binary"),
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": (file_name, content, content_type or "application/octet-stream")},
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    code = payload.get("code")
    if code not in (0, "0", None):
        message = str(payload.get("message") or payload.get("msg") or "RunningHub 上传失败")
        raise RuntimeError(message[:300])
    data = payload.get("data") or {}
    uploaded_name = str(data.get("fileName") or data.get("download_url") or data.get("filename") or data.get("name") or "").strip()
    if not uploaded_name:
        raise RuntimeError("RunningHub 上传成功但未返回 fileName")
    return uploaded_name


def fetch_url_bytes(url: str, expected_prefix: str = "") -> tuple[bytes, str, str]:
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
        response.raise_for_status()
        content_type = response.headers.get("content-type") or mimetypes.guess_type(parsed.path)[0] or "application/octet-stream"
        file_name = Path(parsed.path).name or f"runninghub-source-{uuid.uuid4().hex}"
        return response.content, file_name, content_type
    if parsed.path.startswith("/media/"):
        relative_path = unquote(parsed.path).removeprefix("/media/").lstrip("/")
        absolute_path = (MEDIA_DIR / relative_path).resolve()
        media_root = MEDIA_DIR.resolve()
        if absolute_path == media_root or media_root not in absolute_path.parents or not absolute_path.is_file():
            raise RuntimeError("本地媒体文件不存在")
        content_type = mimetypes.guess_type(absolute_path.name)[0] or expected_prefix or "application/octet-stream"
        return absolute_path.read_bytes(), absolute_path.name, content_type
    raise RuntimeError("无法读取 RunningHub 输入文件")


def _extract_runninghub_data(payload: dict[str, object]) -> object:
    data = payload.get("data")
    if isinstance(data, dict) and "data" in data:
        return data.get("data")
    return data


def get_runninghub_ai_app_demo(api_key: str) -> list[dict[str, object]]:
    candidate_get_paths = [
        "/api/webapp/apiCallDemo",
        "/api/webapp/api-call-demo",
    ]
    last_error = ""
    for path in candidate_get_paths:
        try:
            payload = runninghub_get(
                api_key,
                path,
                params={"apiKey": api_key, "webappId": RUNNINGHUB_WEBAPP_ID, "apiType": 4},
                timeout=60,
            )
            data = _extract_runninghub_data(payload)
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    match = re.search(r'"nodeInfoList"\s*:\s*(\[[\s\S]+?\])', data)
                    if match:
                        return json.loads(match.group(1))
                    data = {}
            if isinstance(data, dict):
                node_info_list = data.get("nodeInfoList") or data.get("NodeInfoList")
                if isinstance(node_info_list, list):
                    return [dict(item) for item in node_info_list if isinstance(item, dict)]
                example = data.get("example") or data.get("apiDemo") or data.get("demo")
                if isinstance(example, str):
                    match = re.search(r'"nodeInfoList"\s*:\s*(\[[\s\S]+?\])', example)
                    if match:
                        return json.loads(match.group(1))
                    try:
                        parsed_example = json.loads(example)
                        node_info_list = parsed_example.get("nodeInfoList") if isinstance(parsed_example, dict) else None
                        if isinstance(node_info_list, list):
                            return [dict(item) for item in node_info_list if isinstance(item, dict)]
                    except Exception:
                        pass
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(f"未能获取 RunningHub API 调用示例，请确认 WebApp 已开放 API 调用。{last_error[:160]}")


def _node_field_type(node: dict[str, object]) -> str:
    for key in ("fieldType", "field_type", "type", "inputType"):
        value = str(node.get(key) or "").strip().upper()
        if value:
            return value
    return ""


def _set_node_upload_value(node: dict[str, object], file_name: str) -> None:
    for key in ("fieldValue", "field_value", "value", "defaultValue"):
        if key in node:
            node[key] = file_name
            return
    node["fieldValue"] = file_name


def build_runninghub_node_info_list(api_key: str, image_file_name: str, video_file_name: str) -> list[dict[str, object]]:
    return [
        {"nodeId": "129", "fieldName": "video", "fieldValue": video_file_name, "description": "载入视频"},
        {"nodeId": "109", "fieldName": "image", "fieldValue": image_file_name, "description": "载入图片"},
        {"nodeId": "127", "fieldName": "value", "fieldValue": "704", "description": "宽度"},
        {"nodeId": "128", "fieldName": "value", "fieldValue": "1280", "description": "高度"},
        {"nodeId": "137", "fieldName": "value", "fieldValue": "0", "description": "总帧数（0=完整视频）"},
        {"nodeId": "136", "fieldName": "value", "fieldValue": "1", "description": "图上有几个人"},
        {"nodeId": "126", "fieldName": "text", "fieldValue": "一个女人在跳舞", "description": "提示词"},
    ]


def create_runninghub_video_migration_task(api_key: str, node_info_list: list[dict[str, object]]) -> str:
    response = requests.post(
        runninghub_endpoint(f"/openapi/v2/run/ai-app/{RUNNINGHUB_WEBAPP_ID}"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "nodeInfoList": node_info_list,
            "instanceType": "default",
            "usePersonalQueue": "false",
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    task_id = str(payload.get("taskId") or "").strip()
    if not task_id:
        message = str(payload.get("errorMessage") or payload.get("message") or "RunningHub 没有返回 taskId")
        raise RuntimeError(message[:300])
    return task_id


def extract_runninghub_output_video(payload: dict[str, object]) -> tuple[str, str]:
    state = str(payload.get("status") or "").strip().lower()
    outputs: object = payload.get("results") or []
    stack = [outputs]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                key_lower = str(key).lower()
                if isinstance(value, str) and (value.startswith("http://") or value.startswith("https://")):
                    path_lower = urlparse(value).path.lower()
                    if "video" in key_lower or path_lower.endswith((".mp4", ".mov", ".webm", ".m4v")):
                        return value, state
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return "", state


def poll_runninghub_video_task(account_id: int, local_task_id: int, api_key: str, runninghub_task_id: str) -> None:
    deadline = time.monotonic() + 90 * 60
    last_state = "waiting"
    while time.monotonic() < deadline:
        if not find_video_migration_task(account_id, local_task_id):
            return
        try:
            payload = runninghub_post(
                api_key,
                "/openapi/v2/query",
                json={"taskId": runninghub_task_id},
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=60,
            )
            video_url, state = extract_runninghub_output_video(payload)
            last_state = state or last_state
            if video_url:
                update_video_migration_task(
                    account_id,
                    local_task_id,
                    stage="step2",
                    video_status="success",
                    runninghub_status=last_state,
                    result_video_url=video_url,
                    error="",
                )
                return
            if last_state in {"failed", "fail", "error", "canceled", "cancelled"}:
                update_video_migration_task(
                    account_id,
                    local_task_id,
                    stage="step2",
                    video_status="error",
                    runninghub_status=last_state,
                    error="RunningHub 视频迁移失败",
                )
                return
            update_video_migration_task(
                account_id,
                local_task_id,
                stage="step2",
                video_status="loading",
                runninghub_status=last_state or "running",
            )
        except Exception as exc:
            update_video_migration_task(
                account_id,
                local_task_id,
                stage="step2",
                video_status="loading",
                runninghub_status=last_state,
                error=str(exc)[:300],
            )
        time.sleep(RUNNINGHUB_VIDEO_POLL_INTERVAL)
    update_video_migration_task(
        account_id,
        local_task_id,
        stage="step2",
        video_status="error",
        runninghub_status=last_state,
        error="RunningHub 视频迁移轮询超时，请稍后重试",
    )


def prepare_and_poll_runninghub_video_task(
    account_id: int,
    local_task_id: int,
    api_key: str,
    target_video_payload: dict[str, object],
) -> None:
    try:
        task = find_video_migration_task(account_id, local_task_id)
        if not task:
            return
        result_image_url = str(task.get("result_image_url") or "").strip()
        if not result_image_url:
            update_video_migration_task(account_id, local_task_id, video_status="error", error="缺少第一步生成的首帧图")
            return
        update_video_migration_task(account_id, local_task_id, stage="step2", video_status="loading", runninghub_status="uploading")
        image_content, image_name, image_type = fetch_url_bytes(result_image_url, "image/png")
        runninghub_image_name = upload_bytes_to_runninghub(api_key, image_content, image_name, image_type, "image")
        video_content = target_video_payload.get("content")
        if not isinstance(video_content, bytes) or not video_content:
            update_video_migration_task(account_id, local_task_id, video_status="error", runninghub_status="error", error="目标视频文件为空")
            return
        runninghub_video_name = upload_bytes_to_runninghub(
            api_key,
            video_content,
            str(target_video_payload.get("filename") or f"target-video-{uuid.uuid4().hex}.mp4"),
            str(target_video_payload.get("content_type") or "video/mp4"),
            "video",
        )
        update_video_migration_task(account_id, local_task_id, runninghub_status="creating", error="")
        node_info_list = build_runninghub_node_info_list(api_key, runninghub_image_name, runninghub_video_name)
        runninghub_task_id = create_runninghub_video_migration_task(api_key, node_info_list)
        update_video_migration_task(
            account_id,
            local_task_id,
            stage="step2",
            video_status="loading",
            runninghub_task_id=runninghub_task_id,
            runninghub_status="waiting",
            error="",
        )
        poll_runninghub_video_task(account_id, local_task_id, api_key, runninghub_task_id)
    except Exception as exc:
        update_video_migration_task(
            account_id,
            local_task_id,
            stage="step2",
            video_status="error",
            runninghub_status="error",
            error=str(exc)[:300],
        )


def _download_note_image(account_id: int, note_id: int, index: int, remote_url: str) -> str | None:
    if not remote_url:
        return None
    try:
        response = requests.get(
            remote_url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.xiaohongshu.com/",
            },
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    suffix = _safe_image_suffix(remote_url, response.headers.get("content-type", ""))
    relative_path = Path("note_images") / str(account_id) / str(note_id) / f"{index}{suffix}"
    absolute_path = MEDIA_DIR / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(response.content)
    return relative_path.as_posix()


def save_note_images(conn, account_id: int, note_db_id: int, image_urls: list[str]) -> None:
    for index, remote_url in enumerate(dict.fromkeys(url for url in image_urls if url)):
        local_path = _download_note_image(account_id, note_db_id, index, remote_url)
        if not local_path:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO note_images(note_id, account_id, image_index, remote_url, local_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (note_db_id, account_id, index, remote_url, local_path),
        )


def clone_note_images(conn, source_note_id: int, target_account_id: int, target_note_id: int) -> int:
    rows = conn.execute(
        """
        SELECT image_index, remote_url, local_path
        FROM note_images
        WHERE note_id = ?
        ORDER BY image_index
        """,
        (source_note_id,),
    ).fetchall()
    copied = 0
    media_root = MEDIA_DIR.resolve()
    for row in rows:
        source_path = (MEDIA_DIR / row["local_path"]).resolve()
        if media_root == source_path or media_root not in source_path.parents or not source_path.exists():
            continue
        suffix = Path(row["local_path"]).suffix or _safe_image_suffix(row["remote_url"])
        relative_path = Path("note_images") / str(target_account_id) / str(target_note_id) / f"{row['image_index']}{suffix}"
        absolute_path = MEDIA_DIR / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, absolute_path)
        conn.execute(
            """
            INSERT OR REPLACE INTO note_images(note_id, account_id, image_index, remote_url, local_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (target_note_id, target_account_id, row["image_index"], row["remote_url"], relative_path.as_posix()),
        )
        copied += 1
    return copied


def media_url(local_path: str) -> str:
    return "/media/" + local_path.replace("\\", "/").lstrip("/")


def normalize_image_urls(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(url).strip() for url in value if str(url).strip()]


def is_video_note(note: dict) -> bool:
    note_type = str(note.get("note_type") or "").strip().lower()
    return note_type in {"video", "瑙嗛"}


def attach_note_images(conn, notes: list[dict]) -> None:
    if not notes:
        return
    note_ids = [note["id"] for note in notes]
    placeholders = ",".join("?" for _ in note_ids)
    rows = conn.execute(
        f"""
        SELECT note_id, remote_url, local_path
        FROM note_images
        WHERE note_id IN ({placeholders})
        ORDER BY note_id, image_index
        """,
        note_ids,
    ).fetchall()
    images_by_note: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        images_by_note.setdefault(row["note_id"], []).append(
            {"url": media_url(row["local_path"]), "remote_url": row["remote_url"]}
        )
    for note in notes:
        remote_urls = normalize_image_urls(from_json(note["image_urls_json"], []))
        note["image_urls"] = remote_urls
        note["images"] = images_by_note.get(note["id"]) or [
            {"url": url, "remote_url": url} for url in remote_urls
        ]


def first_cover_url(conn, note: dict) -> str | None:
    row = conn.execute(
        """
        SELECT remote_url, local_path
        FROM note_images
        WHERE note_id = ?
        ORDER BY image_index
        LIMIT 1
        """,
        (note["id"],),
    ).fetchone()
    if row and row["local_path"]:
        local_path = (MEDIA_DIR / row["local_path"]).resolve()
        media_root = MEDIA_DIR.resolve()
        if media_root in local_path.parents and local_path.is_file():
            mime_type = mimetypes.guess_type(local_path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"
    if row and row["remote_url"]:
        return row["remote_url"]
    image_urls = normalize_image_urls(from_json(note.get("image_urls_json"), []))
    return image_urls[0] if image_urls else None


def delete_note_images(conn, account_id: int, note_id: int) -> None:
    rows = conn.execute(
        "SELECT local_path FROM note_images WHERE note_id = ? AND account_id = ?",
        (note_id, account_id),
    ).fetchall()
    for row in rows:
        image_path = (MEDIA_DIR / row["local_path"]).resolve()
        media_root = MEDIA_DIR.resolve()
        if media_root == image_path or media_root not in image_path.parents:
            continue
        try:
            image_path.unlink(missing_ok=True)
        except OSError:
            pass
    note_dir = MEDIA_DIR / "note_images" / str(account_id) / str(note_id)
    shutil.rmtree(note_dir, ignore_errors=True)
    conn.execute("DELETE FROM note_images WHERE note_id = ? AND account_id = ?", (note_id, account_id))


def save_crawled_notes(
    notes: list[dict],
    account_id: int,
    competitor_id: int | None = None,
    source: str = "crawl",
) -> dict[str, int]:
    stats = {"received": len(notes), "saved": 0, "skipped": 0}
    with connect() as conn:
        for note in notes:
            if is_video_note(note):
                stats["skipped"] += 1
                continue
            note_id, note_url = _note_identity(note)
            if not note_id and not note_url:
                stats["skipped"] += 1
                continue
            if _note_exists(conn, account_id, note_id, note_url):
                stats["skipped"] += 1
                continue

            image_urls = normalize_image_urls(note.get("image_list", []))
            cursor = conn.execute(
                """
                INSERT INTO notes(
                    account_id, competitor_id, source, platform_note_id, note_url, note_type,
                    author_name, title, body, tags_json, image_urls_json, like_count, collect_count,
                    comment_count, share_count, score, summary, ai_score_json, published_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    competitor_id,
                    source,
                    note_id,
                    note_url,
                    note.get("note_type"),
                    note.get("nickname", ""),
                    note.get("title", ""),
                    note.get("desc", ""),
                    as_json(note.get("tags", [])),
                    as_json(image_urls),
                    int(note.get("liked_count") or 0),
                    int(note.get("collected_count") or 0),
                    int(note.get("comment_count") or 0),
                    int(note.get("share_count") or 0),
                    0,
                    "未打分",
                    "{}",
                    note.get("upload_time") or None,
                    note.get("raw_json", "{}"),
                ),
            )
            save_note_images(conn, account_id, cursor.lastrowid, image_urls)
            stats["saved"] += 1
    return stats


def sync_notes_from_account(source_account_id: int, target_account_id: int) -> dict[str, int]:
    stats = {"copied": 0, "skipped": 0, "images_copied": 0}
    with connect() as conn:
        notes = conn.execute(
            """
            SELECT *
            FROM notes
            WHERE account_id = ? AND COALESCE(is_hidden, 0) = 0
            ORDER BY id
            """,
            (source_account_id,),
        ).fetchall()
        for note in notes:
            note_id, note_url = _note_identity(note)
            if _note_exists(conn, target_account_id, note_id, note_url):
                stats["skipped"] += 1
                continue
            cursor = conn.execute(
                """
                INSERT INTO notes(
                    account_id, competitor_id, source, platform_note_id, note_url, note_type,
                    author_name, title, body, tags_json, image_urls_json, like_count, collect_count,
                    comment_count, share_count, score, summary, ai_score_json, published_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_account_id,
                    None,
                    note["source"],
                    note["platform_note_id"],
                    note["note_url"],
                    note["note_type"],
                    note["author_name"],
                    note["title"],
                    note["body"],
                    note["tags_json"],
                    note["image_urls_json"],
                    int(note["like_count"] or 0),
                    int(note["collect_count"] or 0),
                    int(note["comment_count"] or 0),
                    int(note["share_count"] or 0),
                    int(note["score"] or 0),
                    note["summary"],
                    note.get("ai_score_json") or "{}",
                    note.get("published_at"),
                    note["raw_json"],
                ),
            )
            stats["images_copied"] += clone_note_images(conn, note["id"], target_account_id, cursor.lastrowid)
            stats["copied"] += 1
    return stats


def crawl_result_message(stats: dict[str, int]) -> str:
    saved = int(stats.get("saved", 0))
    skipped = int(stats.get("skipped", 0))
    received = int(stats.get("received", 0))
    if saved:
        return f"采集完成：新增 {saved} 条，跳过 {skipped} 条重复内容，本轮返回 {received} 条。今日额度增加 {saved}。"
    if skipped:
        return f"本轮没有新增内容，{skipped} 条都已存在，本轮返回 {received} 条。今日额度不会增加。"
    return "采集已结束，但没有保存到笔记。可能是链接或 Cookie 可用，但接口没有返回可解析内容。今日额度不会增加。"


def build_prompt_preview_data_url(prompt: str, output_target: str, aspect_ratio: str, resolution: str) -> str:
    safe_prompt = (prompt or "等待生成").strip()[:72]
    safe_target = (output_target or "未设置输出目标").strip()[:24]
    safe_ratio = (aspect_ratio or "9:16").strip()[:12]
    safe_resolution = (resolution or "1K").strip()[:12]
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="720" height="1280" viewBox="0 0 720 1280">
      <defs>
        <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#fff1f2"/>
          <stop offset="52%" stop-color="#fff7ed"/>
          <stop offset="100%" stop-color="#fefce8"/>
        </linearGradient>
      </defs>
      <rect width="720" height="1280" rx="36" fill="url(#bg)"/>
      <rect x="52" y="62" width="158" height="54" rx="27" fill="#111827" fill-opacity="0.78"/>
      <text x="131" y="96" text-anchor="middle" font-size="24" fill="#ffffff" font-family="Arial, PingFang SC, Microsoft YaHei">文生图结果</text>
      <text x="54" y="188" font-size="28" fill="#be123c" font-family="Arial, PingFang SC, Microsoft YaHei">Prompt</text>
      <foreignObject x="54" y="216" width="612" height="380">
        <div xmlns="http://www.w3.org/1999/xhtml" style="font-family: Arial, PingFang SC, Microsoft YaHei; font-size: 42px; line-height: 1.45; color: #111827; font-weight: 700;">
          {html.escape(safe_prompt)}
        </div>
      </foreignObject>
      <rect x="54" y="716" width="612" height="232" rx="30" fill="#ffffff" fill-opacity="0.72" stroke="#fecdd3"/>
      <text x="92" y="784" font-size="30" fill="#475569" font-family="Arial, PingFang SC, Microsoft YaHei">输出目标</text>
      <text x="92" y="830" font-size="42" fill="#0f172a" font-family="Arial, PingFang SC, Microsoft YaHei" font-weight="700">{html.escape(safe_target)}</text>
      <text x="92" y="894" font-size="26" fill="#64748b" font-family="Arial, PingFang SC, Microsoft YaHei">图像规格 {html.escape(safe_ratio)}    分辨率 {html.escape(safe_resolution)}</text>
      <text x="54" y="1180" font-size="24" fill="#94a3b8" font-family="Arial, PingFang SC, Microsoft YaHei">当前版本为页面联调占位结果，可后续接入真实绘图服务</text>
    </svg>
    """.strip()
    return f"data:image/svg+xml;charset=utf-8,{url_quote(svg)}"


def build_image_task(item: dict, index: int, selected_id: int | None = None) -> dict[str, object]:
    label = str(item.get("label") or "").strip()
    analysis = str(item.get("analysis") or "").strip()
    image_url = str(item.get("image_url") or "").strip()

    mode = "prompt" if ("Prompt：" in analysis or label.startswith("文生图")) else "remix"
    prompt_text = ""
    output_target = "未设置"
    aspect_ratio = "9:16"
    resolution = "1K"

    if mode == "prompt":
        prompt_match = re.search(r"Prompt[:：]\s*([^|]+)", analysis)
        target_match = re.search(r"输出目标[:：]\s*([^|]+)", analysis)
        ratio_match = re.search(r"图像规格[:：]\s*([^|]+)", analysis)
        resolution_match = re.search(r"分辨率[:：]\s*([^|]+)", analysis)
        prompt_text = (prompt_match.group(1).strip() if prompt_match else label.replace("文生图｜", "").strip()) or "未填写 Prompt"
        output_target = target_match.group(1).strip() if target_match else "未设置"
        aspect_ratio = ratio_match.group(1).strip() if ratio_match else "9:16"
        resolution = resolution_match.group(1).strip() if resolution_match else "1K"
    else:
        prompt_text = label or "图生图任务"
        output_target = "图生图"
        aspect_ratio = "参考原图"
        resolution = "跟随结果"

    status = "success" if image_url else "loading"
    return {
        "id": item["id"],
        "mode": mode,
        "tab_label": f"任务{index}",
        "prompt": prompt_text,
        "output_target": output_target,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "image_url": image_url,
        "status": status,
        "is_selected": bool(selected_id and item["id"] == selected_id),
    }


def split_image_tasks(account_id: int, selected_id: int | None = None) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    prompt_tasks: list[dict[str, object]] = []
    remix_tasks: list[dict[str, object]] = []
    with IMAGE_TASK_LOCK:
        tasks = [dict(task) for task in IMAGE_RUNTIME_TASKS.get(account_id, [])]
    prompt_index = 1
    remix_index = 1
    for task in tasks:
        display_task = dict(task)
        display_task["is_selected"] = bool(selected_id and int(display_task["id"]) == selected_id)
        if display_task.get("status") == "loading":
            started_at = float(display_task.get("started_at") or 0)
            elapsed = max(0, int(time.time() - started_at)) if started_at else 0
            display_task["eta_seconds"] = max(0, 120 - elapsed)
        elif display_task.get("video_status") == "loading":
            started_at = float(display_task.get("video_started_at") or 0)
            estimate = int(float(display_task.get("estimated_video_seconds") or 0))
            elapsed = max(0, int(time.time() - started_at)) if started_at else 0
            display_task["eta_seconds"] = max(0, estimate - elapsed) if estimate else ""
        else:
            display_task["eta_seconds"] = ""
        if display_task["mode"] == "prompt":
            display_task["tab_label"] = f"任务 {prompt_index}"
            prompt_index += 1
            prompt_tasks.append(display_task)
        else:
            display_task["tab_label"] = f"任务 {remix_index}"
            remix_index += 1
            remix_tasks.append(display_task)
    return prompt_tasks, remix_tasks


def find_runtime_image_task(account_id: int, task_id: int) -> dict[str, object] | None:
    with IMAGE_TASK_LOCK:
        for task in IMAGE_RUNTIME_TASKS.get(account_id, []):
            if int(task.get("id", 0)) == task_id:
                return task
    return None


def update_runtime_image_task(account_id: int, task_id: int, **updates: object) -> bool:
    with IMAGE_TASK_LOCK:
        for task in IMAGE_RUNTIME_TASKS.get(account_id, []):
            if int(task.get("id", 0)) == task_id:
                task.update(updates)
                return True
    return False


def split_video_migration_tasks(account_id: int, selected_id: int | None = None) -> list[dict[str, object]]:
    with VIDEO_MIGRATION_TASK_LOCK:
        tasks = [dict(task) for task in VIDEO_MIGRATION_RUNTIME_TASKS.get(account_id, [])]
    display_tasks: list[dict[str, object]] = []
    for task in tasks:
        display_task = dict(task)
        display_index = int(display_task.get("display_index") or display_task.get("id") or 0)
        display_task["display_index"] = display_index
        display_task["tab_label"] = f"任务 {display_index}"
        display_task["is_selected"] = bool(selected_id and int(display_task["id"]) == selected_id)
        if display_task.get("status") == "loading":
            started_at = float(display_task.get("started_at") or 0)
            elapsed = max(0, int(time.time() - started_at)) if started_at else 0
            display_task["eta_seconds"] = max(0, 120 - elapsed)
        else:
            display_task["eta_seconds"] = ""
        display_tasks.append(display_task)
    return display_tasks


def find_video_migration_task(account_id: int, task_id: int) -> dict[str, object] | None:
    with VIDEO_MIGRATION_TASK_LOCK:
        for task in VIDEO_MIGRATION_RUNTIME_TASKS.get(account_id, []):
            if int(task.get("id", 0)) == task_id:
                return task
    return None


def update_video_migration_task(account_id: int, task_id: int, **updates: object) -> bool:
    with VIDEO_MIGRATION_TASK_LOCK:
        for task in VIDEO_MIGRATION_RUNTIME_TASKS.get(account_id, []):
            if int(task.get("id", 0)) == task_id:
                task.update(updates)
                return True
    return False


def next_video_migration_display_index(account_id: int) -> int:
    with VIDEO_MIGRATION_TASK_LOCK:
        current_value = VIDEO_MIGRATION_DISPLAY_COUNTERS.get(account_id)
        if current_value is None:
            current_value = max(
                [
                    int(task.get("display_index") or task.get("id") or 0)
                    for task in VIDEO_MIGRATION_RUNTIME_TASKS.get(account_id, [])
                ],
                default=0,
            )
        next_value = current_value + 1
        VIDEO_MIGRATION_DISPLAY_COUNTERS[account_id] = next_value
        return next_value


IMAGE_MODEL_OPTIONS = {
    "gpt-image-2-text-to-image": {
        "label": "GPT-image-2",
        "payload": "gpt-image-2-text-to-image",
    },
    "nano-banana-2": {
        "label": "Google-香蕉-2",
        "payload": "nano-banana-2",
    },
}


def create_kie_text_to_image_task(api_key: str, prompt: str, aspect_ratio: str, resolution: str, image_model: str) -> str:
    model = IMAGE_MODEL_OPTIONS.get(image_model, IMAGE_MODEL_OPTIONS["gpt-image-2-text-to-image"])["payload"]
    input_payload = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio or "auto",
    }
    if model == "nano-banana-2":
        input_payload.update(
            {
                "image_input": [],
                "resolution": resolution or "1K",
                "output_format": "png",
            }
        )
    response = requests.post(
        "https://api.kie.ai/api/v1/jobs/createTask",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": input_payload,
        },
        proxies=get_kie_proxies(),
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    task_id = str((payload.get("data") or {}).get("taskId") or "").strip()
    if not task_id:
        message = str(payload.get("msg") or "KIE 没有返回 taskId")
        raise RuntimeError(message[:300])
    return task_id


def create_kie_image_to_image_task(
    api_key: str,
    prompt: str,
    input_urls: list[str],
    aspect_ratio: str,
    resolution: str,
    image_model: str,
) -> str:
    if not input_urls:
        raise RuntimeError("请先上传至少 1 张参考图")
    if image_model == "nano-banana-2":
        model = "nano-banana-2"
        input_payload = {
            "prompt": prompt,
            "image_input": input_urls[:4],
            "aspect_ratio": aspect_ratio or "auto",
            "resolution": resolution or "1K",
            "output_format": "png",
        }
    else:
        model = "gpt-image-2-image-to-image"
        input_payload = {
            "prompt": prompt,
            "input_urls": input_urls[:4],
            "aspect_ratio": aspect_ratio or "auto",
        }
    response = requests.post(
        "https://api.kie.ai/api/v1/jobs/createTask",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": input_payload,
        },
        proxies=get_kie_proxies(),
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    task_id = str((payload.get("data") or {}).get("taskId") or "").strip()
    if not task_id:
        message = str(payload.get("msg") or "KIE 没有返回 taskId")
        raise RuntimeError(message[:300])
    return task_id


def extract_kie_result_url(result_json: object) -> str:
    if isinstance(result_json, str):
        if not result_json.strip():
            return ""
        try:
            result_json = json.loads(result_json)
        except json.JSONDecodeError:
            return ""
    if not isinstance(result_json, dict):
        return ""
    result_urls = result_json.get("resultUrls") or result_json.get("urls") or result_json.get("images")
    if isinstance(result_urls, list):
        for value in result_urls:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = str(value.get("url") or value.get("imageUrl") or "").strip()
                if nested:
                    return nested
    for key in ("url", "imageUrl", "resultUrl"):
        value = str(result_json.get(key) or "").strip()
        if value:
            return value
    return ""


def poll_kie_text_to_image_task(account_id: int, local_task_id: int, kie_task_id: str) -> None:
    api_key = get_app_setting("kie_api_key")
    if not api_key:
        update_runtime_image_task(account_id, local_task_id, status="error", error="KIE API Key 未配置")
        return

    deadline = time.monotonic() + 15 * 60
    interval = 2.5
    last_state = "waiting"
    while time.monotonic() < deadline:
        if not find_runtime_image_task(account_id, local_task_id):
            return
        try:
            response = requests.get(
                "https://api.kie.ai/api/v1/jobs/recordInfo",
                params={"taskId": kie_task_id},
                headers={"Authorization": f"Bearer {api_key}"},
                proxies=get_kie_proxies(),
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or {}
            state = str(data.get("state") or "").strip().lower()
            progress = data.get("progress")
            last_state = state or last_state
            if state == "success":
                image_url = extract_kie_result_url(data.get("resultJson"))
                if image_url:
                    update_runtime_image_task(
                        account_id,
                        local_task_id,
                        status="success",
                        backend_status=state,
                        progress=100,
                        image_url=image_url,
                        error="",
                    )
                    return
                update_runtime_image_task(account_id, local_task_id, status="error", backend_status=state, error="KIE 成功但未返回图片链接")
                return
            if state == "fail":
                message = str(data.get("failMsg") or payload.get("msg") or "KIE 生图失败").strip()
                update_runtime_image_task(account_id, local_task_id, status="error", backend_status=state, error=message[:300])
                return
            update_runtime_image_task(
                account_id,
                local_task_id,
                status="loading",
                backend_status=state or "generating",
                progress=progress if progress is not None else "",
            )
        except Exception as exc:
            update_runtime_image_task(account_id, local_task_id, status="loading", backend_status=last_state, error=str(exc)[:300])
        time.sleep(interval)
        interval = min(12.0, interval * 1.25)

    update_runtime_image_task(account_id, local_task_id, status="error", backend_status=last_state, error="KIE 生图轮询超时，请稍后重试")


def prepare_and_poll_kie_image_task(
    account_id: int,
    local_task_id: int,
    mode: str,
    api_key: str,
    final_prompt: str,
    aspect_ratio: str,
    resolution: str,
    image_model: str,
    reference_uploads: list[dict[str, object]] | None = None,
    reference_url: str = "",
) -> None:
    try:
        update_runtime_image_task(account_id, local_task_id, status="loading", backend_status="uploading" if mode == "remix" else "creating")
        if mode == "remix":
            input_urls: list[str] = []
            if reference_uploads:
                for item in reference_uploads[:4]:
                    content = item.get("content")
                    if not isinstance(content, bytes) or not content:
                        continue
                    input_urls.append(
                        upload_reference_bytes_to_kie(
                            api_key,
                            content,
                            str(item.get("filename") or f"reference-{uuid.uuid4().hex}.jpg"),
                            str(item.get("content_type") or "application/octet-stream"),
                            account_id,
                        )
                    )
            elif reference_url.startswith("data:image/"):
                input_urls = [upload_reference_data_url_to_kie(api_key, reference_url, account_id)]
            elif urlparse(reference_url).path.startswith("/media/"):
                input_urls = [upload_reference_media_url_to_kie(api_key, reference_url, account_id)]
            elif reference_url:
                input_urls = [reference_url]

            if not input_urls:
                update_runtime_image_task(account_id, local_task_id, status="error", backend_status="error", error="请先上传至少 1 张参考图")
                return
            update_runtime_image_task(account_id, local_task_id, backend_status="creating")
            kie_task_id = create_kie_image_to_image_task(
                api_key,
                final_prompt,
                input_urls,
                aspect_ratio,
                resolution,
                image_model,
            )
        else:
            kie_task_id = create_kie_text_to_image_task(
                api_key,
                final_prompt,
                aspect_ratio,
                resolution,
                image_model,
            )
        update_runtime_image_task(account_id, local_task_id, backend_task_id=kie_task_id, backend_status="waiting", error="")
        poll_kie_text_to_image_task(account_id, local_task_id, kie_task_id)
    except Exception as exc:
        update_runtime_image_task(account_id, local_task_id, status="error", backend_status="error", error=str(exc)[:300])


def poll_kie_video_migration_image_task(account_id: int, local_task_id: int, kie_task_id: str) -> None:
    api_key = get_app_setting("kie_api_key")
    if not api_key:
        update_video_migration_task(account_id, local_task_id, status="error", error="KIE API Key 未配置")
        return

    deadline = time.monotonic() + 15 * 60
    interval = 2.5
    last_state = "waiting"
    while time.monotonic() < deadline:
        if not find_video_migration_task(account_id, local_task_id):
            return
        try:
            response = requests.get(
                "https://api.kie.ai/api/v1/jobs/recordInfo",
                params={"taskId": kie_task_id},
                headers={"Authorization": f"Bearer {api_key}"},
                proxies=get_kie_proxies(),
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or {}
            state = str(data.get("state") or "").strip().lower()
            progress = data.get("progress")
            last_state = state or last_state
            if state == "success":
                image_url = extract_kie_result_url(data.get("resultJson"))
                if image_url:
                    update_video_migration_task(
                        account_id,
                        local_task_id,
                        status="success",
                        backend_status=state,
                        progress=100,
                        result_image_url=image_url,
                        error="",
                    )
                    return
                update_video_migration_task(account_id, local_task_id, status="error", backend_status=state, error="KIE 成功但未返回图片链接")
                return
            if state == "fail":
                message = str(data.get("failMsg") or payload.get("msg") or "KIE 生图失败").strip()
                update_video_migration_task(account_id, local_task_id, status="error", backend_status=state, error=message[:300])
                return
            update_video_migration_task(
                account_id,
                local_task_id,
                status="loading",
                backend_status=state or "generating",
                progress=progress if progress is not None else "",
            )
        except Exception as exc:
            update_video_migration_task(account_id, local_task_id, status="loading", backend_status=last_state, error=str(exc)[:300])
        time.sleep(interval)
        interval = min(12.0, interval * 1.25)

    update_video_migration_task(account_id, local_task_id, status="error", backend_status=last_state, error="KIE 首帧生成轮询超时，请稍后重试")


def prepare_and_poll_video_migration_step1(
    account_id: int,
    local_task_id: int,
    api_key: str,
    source_payload: dict[str, object],
    face_payload: dict[str, object],
) -> None:
    try:
        update_video_migration_task(account_id, local_task_id, status="loading", backend_status="uploading", error="")
        input_urls: list[str] = []
        for item in (source_payload, face_payload):
            content = item.get("content")
            if not isinstance(content, bytes) or not content:
                continue
            input_urls.append(
                upload_reference_bytes_to_kie(
                    api_key,
                    content,
                    str(item.get("filename") or f"video-migration-{uuid.uuid4().hex}.jpg"),
                    str(item.get("content_type") or "application/octet-stream"),
                    account_id,
                )
            )
        if len(input_urls) < 2:
            update_video_migration_task(account_id, local_task_id, status="error", backend_status="error", error="请先上传图 1 和图 2")
            return
        update_video_migration_task(account_id, local_task_id, backend_status="creating")
        final_prompt = "把图1的脸换成图2。保持图1的人物姿态、构图、服装、背景、光线和画面风格，只替换脸部身份。输出竖版 9:16 图片。"
        kie_task_id = create_kie_image_to_image_task(
            api_key,
            final_prompt,
            input_urls[:2],
            "9:16",
            "1K",
            "nano-banana-2",
        )
        update_video_migration_task(
            account_id,
            local_task_id,
            backend_task_id=kie_task_id,
            backend_status="waiting",
            final_prompt=final_prompt,
            error="",
        )
        poll_kie_video_migration_image_task(account_id, local_task_id, kie_task_id)
    except Exception as exc:
        update_video_migration_task(account_id, local_task_id, status="error", backend_status="error", error=str(exc)[:300])


@app.get("/", response_class=HTMLResponse)
def home(request: Request, page: str = "accounts") -> HTMLResponse:
    current = get_current_account()
    valid_pages = {"accounts", "brand", "competitors", "content", "copywriting", "images", "video_migration", "settings"}
    current_page = page if page in valid_pages else "accounts"
    show_latest_copywriting = str(request.query_params.get("show_latest") or "").strip() == "1"
    copywriting_result_id = str(request.query_params.get("copy_id") or "").strip()
    copywriting_status = "idle"
    copywriting_status_label = "待生成"
    show_latest_image = str(request.query_params.get("show_latest_image") or "").strip() == "1"
    image_result_id = str(request.query_params.get("image_id") or "").strip()
    image_task_id = str(request.query_params.get("task_id") or "").strip()
    video_migration_task_id = str(request.query_params.get("video_task_id") or "").strip()
    image_status = "idle"
    image_status_label = "待生成"
    if str(request.query_params.get("copywriting_error") or "").strip():
        copywriting_status = "error"
        copywriting_status_label = "生成失败"
    elif str(request.query_params.get("copywriting_notice") or "").strip() or copywriting_result_id:
        copywriting_status = "success"
        copywriting_status_label = "已生成"
    if str(request.query_params.get("image_error") or "").strip():
        image_status = "error"
        image_status_label = "生成失败"
    elif str(request.query_params.get("image_notice") or "").strip() or image_result_id:
        image_status = "success"
        image_status_label = "已生成"
    daily_crawl_used = get_daily_crawl_used(current["id"]) if current else 0
    daily_crawl_remaining = max(0, DAILY_CRAWL_LIMIT - daily_crawl_used)
    with connect() as conn:
        accounts = conn.execute(
            "SELECT id, name, phone, login_status, login_method, is_current, created_at FROM accounts ORDER BY id DESC"
        ).fetchall()
        competitors = []
        notes = []
        drafts = []
        published = []
        brand_profile = None
        image_refs = []
        shareable_accounts = []
        latest_generation = None
        copywriting_result = None
        latest_image_ref = None
        image_result = None
        image_task_count = 0
        image_tasks_prompt = []
        image_tasks_remix = []
        video_migration_tasks = []
        saved_setting_rows = conn.execute(
            """
            SELECT key, value, created_at, updated_at
            FROM app_settings
            WHERE key IN ('kie_api_key', 'deepseek_api_key', 'kie_proxy', 'runninghub_api_key')
            """
        ).fetchall()
        saved_settings = {row["key"]: bool(str(row["value"]).strip()) for row in saved_setting_rows}
        setting_values = {row["key"]: str(row["value"] or "").strip() for row in saved_setting_rows}
        setting_values["kie_proxy"] = setting_values.get("kie_proxy") or DEFAULT_KIE_PROXY
        saved_settings["kie_proxy"] = bool(setting_values["kie_proxy"])
        setting_details = {
            row["key"]: {
                "masked_value": mask_secret(row["value"]),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "is_configured": bool(str(row["value"]).strip()),
            }
            for row in saved_setting_rows
            if bool(str(row["value"]).strip())
        }
        if current:
            brand_profile = conn.execute(
                "SELECT * FROM brand_profiles WHERE account_id = ? LIMIT 1", (current["id"],)
            ).fetchone()
            shareable_accounts = conn.execute(
                """
                SELECT id, name, login_status
                FROM accounts
                WHERE id != ?
                ORDER BY id DESC
                """,
                (current["id"],),
            ).fetchall()
            competitors = conn.execute(
                "SELECT * FROM competitors WHERE account_id = ? ORDER BY id DESC", (current["id"],)
            ).fetchall()
            notes = conn.execute(
                "SELECT * FROM notes WHERE account_id = ? AND COALESCE(is_hidden, 0) = 0 ORDER BY score DESC, id DESC LIMIT 50",
                (current["id"],),
            ).fetchall()
            drafts = conn.execute(
                "SELECT * FROM drafts WHERE account_id = ? ORDER BY id DESC LIMIT 20", (current["id"],)
            ).fetchall()
            published = conn.execute(
                "SELECT * FROM published_items WHERE account_id = ? ORDER BY id DESC LIMIT 20", (current["id"],)
            ).fetchall()
            image_refs = conn.execute(
                "SELECT * FROM image_references WHERE account_id = ? ORDER BY id DESC LIMIT 20", (current["id"],)
            ).fetchall()
            latest_image_ref = image_refs[0] if image_refs else None
            latest_generation = conn.execute(
                """
                SELECT *
                FROM copy_generations
                WHERE account_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (current["id"],),
            ).fetchone()
            selected_generation = None
            if current_page == "copywriting":
                if copywriting_result_id.isdigit():
                    selected_generation = conn.execute(
                        """
                        SELECT *
                        FROM copy_generations
                        WHERE id = ? AND account_id = ?
                        LIMIT 1
                        """,
                        (int(copywriting_result_id), current["id"]),
                    ).fetchone()
                elif show_latest_copywriting:
                    selected_generation = latest_generation
            if current_page == "images":
                selected_image_id = int(image_task_id) if image_task_id.isdigit() else None
                image_tasks_prompt, image_tasks_remix = split_image_tasks(current["id"], selected_image_id)
                image_task_count = len(image_tasks_prompt) + len(image_tasks_remix)
            if current_page == "video_migration":
                selected_video_task_id = int(video_migration_task_id) if video_migration_task_id.isdigit() else None
                video_migration_tasks = split_video_migration_tasks(current["id"], selected_video_task_id)
            attach_note_images(conn, notes)
            for note in notes:
                note["ai_score"] = from_json(note.get("ai_score_json"), {})
                if note["ai_score"]:
                    normalize_score_result(note["ai_score"])
                tags = from_json(note.get("tags_json"), [])
                note["keywords"] = extract_note_keywords(note.get("body"), tags)
                note["display_body"] = strip_note_topics(note.get("body"))
                note["publication_age_days"] = publication_age_days(note.get("published_at"))
            if selected_generation:
                copywriting_result = {
                    "id": selected_generation["id"],
                    "titles": from_json(selected_generation.get("titles_json"), []),
                    "body": selected_generation.get("body") or "",
                    "tags": from_json(selected_generation.get("tags_json"), []),
                    "created_at": selected_generation.get("created_at"),
                    "source_note_id": selected_generation.get("source_note_id"),
                }

    for draft in drafts:
        draft["duplicate_segments"] = from_json(draft["duplicate_segments_json"], [])
    copywriting_form = build_copywriting_prefill(request, latest_generation)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "accounts": accounts,
            "current": current,
            "current_page": current_page,
            "competitors": competitors,
            "notes": notes,
            "drafts": drafts,
            "published": published,
            "brand_profile": brand_profile,
            "image_refs": image_refs,
            "shareable_accounts": shareable_accounts,
            "copywriting_types": COPYWRITING_TYPES,
            "copywriting_goals": COPYWRITING_GOALS,
            "copywriting_word_counts": COPYWRITING_WORD_COUNTS,
            "copywriting_differentiation_levels": COPYWRITING_DIFFERENTIATION_LEVELS,
            "copywriting_form": copywriting_form,
            "copywriting_result": copywriting_result,
            "has_copywriting_history": bool(latest_generation),
            "copywriting_status": copywriting_status,
            "copywriting_status_label": copywriting_status_label,
            "image_result": image_result,
            "has_image_history": bool(latest_image_ref),
            "image_status": image_status,
            "image_status_label": image_status_label,
            "image_task_count": image_task_count,
            "image_tasks_prompt": image_tasks_prompt,
            "image_tasks_remix": image_tasks_remix,
            "video_migration_tasks": video_migration_tasks,
            "daily_crawl_limit": DAILY_CRAWL_LIMIT,
            "daily_crawl_used": daily_crawl_used,
            "daily_crawl_remaining": daily_crawl_remaining,
            "saved_settings": saved_settings,
            "setting_details": setting_details,
            "setting_values": setting_values,
        },
    )


@app.post("/settings")
def save_settings(
    setting_group: str = Form(""),
    kie_api_key: str = Form(""),
    deepseek_api_key: str = Form(""),
    runninghub_api_key: str = Form(""),
    kie_proxy: str = Form(""),
) -> RedirectResponse:
    setting_group = setting_group.strip()
    if setting_group == "kie":
        updates = {"kie_api_key": kie_api_key.strip()}
        notice = "KIE API Key 已保存。"
    elif setting_group == "deepseek":
        updates = {"deepseek_api_key": deepseek_api_key.strip()}
        notice = "DeepSeek API Key 已保存。"
    elif setting_group == "runninghub":
        updates = {"runninghub_api_key": runninghub_api_key.strip()}
        notice = "RunningHub API Key 已保存。"
    elif setting_group == "proxy":
        updates = {"kie_proxy": kie_proxy.strip() or DEFAULT_KIE_PROXY}
        notice = "代理设置已保存。"
    else:
        return redirect_page_with_error("settings", "不支持保存这个设置。", query_key="settings_notice")

    with connect() as conn:
        for key, new_value in updates.items():
            if not new_value:
                continue
            conn.execute(
                """
                INSERT INTO app_settings(key, value, created_at, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, new_value),
            )
    if setting_group in {"kie", "deepseek", "runninghub"} and not next(iter(updates.values())):
        notice = "未填写新 API Key，已保留原设置。"
    return redirect_page_with_notice("settings", notice, query_key="settings_notice")


@app.post("/settings/delete")
def delete_setting(setting_key: str = Form(...)) -> RedirectResponse:
    allowed_keys = {"kie_api_key", "deepseek_api_key", "kie_proxy", "runninghub_api_key"}
    if setting_key not in allowed_keys:
        return redirect_page_with_error("settings", "不支持删除这个设置。", query_key="settings_notice")
    with connect() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (setting_key,))
    notice = "代理设置已恢复默认。" if setting_key == "kie_proxy" else "密钥已删除。"
    return redirect_page_with_notice("settings", notice, query_key="settings_notice")


@app.post("/accounts")
def create_account(name: str = Form(...), phone: str = Form(""), cookie: str = Form(...)) -> RedirectResponse:
    clean_cookie = clean_pasted_text(cookie)
    with connect() as conn:
        has_accounts = conn.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()["count"] > 0
        conn.execute(
            """
            INSERT INTO accounts(name, phone, cookie, login_method, login_status, is_current)
            VALUES (?, ?, ?, 'manual_cookie', 'logged_in', ?)
            """,
            (clean_pasted_text(name), clean_pasted_text(phone), clean_cookie, 0 if has_accounts else 1),
        )
    return redirect_page("brand")


@app.post("/accounts/switch")
def switch_account(account_id: int = Form(...), page: str = Form("accounts")) -> RedirectResponse:
    with connect() as conn:
        conn.execute("UPDATE accounts SET is_current = CASE WHEN id = ? THEN 1 ELSE 0 END", (account_id,))
    if page in {"accounts", "brand", "competitors", "content", "copywriting", "images", "video_migration", "settings"}:
        return redirect_page(page)
    return redirect_home()


@app.post("/accounts/update-cookie")
def update_account_cookie(account_id: int = Form(...), cookie: str = Form(...)) -> RedirectResponse:
    clean_cookie = clean_pasted_text(cookie)
    with connect() as conn:
        conn.execute(
            """
            UPDATE accounts
            SET cookie = ?, login_status = 'logged_in', login_method = 'manual_cookie',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (clean_cookie, account_id),
        )
    return redirect_home()


@app.post("/accounts/logout")
def logout_account(account_id: int = Form(...)) -> RedirectResponse:
    with connect() as conn:
        conn.execute(
            """
            UPDATE accounts
            SET cookie = '', login_status = 'logged_out', is_current = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )
        next_account = conn.execute(
            "SELECT id FROM accounts WHERE id != ? AND cookie != '' ORDER BY id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        if next_account:
            conn.execute("UPDATE accounts SET is_current = CASE WHEN id = ? THEN 1 ELSE 0 END", (next_account["id"],))
    return redirect_home()


@app.post("/accounts/delete")
def delete_account(account_id: int = Form(...)) -> RedirectResponse:
    with connect() as conn:
        was_current = conn.execute("SELECT is_current FROM accounts WHERE id = ?", (account_id,)).fetchone()
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        shutil.rmtree(MEDIA_DIR / "note_images" / str(account_id), ignore_errors=True)
        if was_current and was_current["is_current"]:
            next_account = conn.execute("SELECT id FROM accounts ORDER BY id DESC LIMIT 1").fetchone()
            if next_account:
                conn.execute("UPDATE accounts SET is_current = CASE WHEN id = ? THEN 1 ELSE 0 END", (next_account["id"],))
    return redirect_home()


@app.post("/competitors")
def add_competitor(name: str = Form(""), profile_url: str = Form(...)) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    clean_profile_url = clean_pasted_text(profile_url)
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM competitors WHERE account_id = ? AND profile_url = ? LIMIT 1",
            (current["id"], clean_profile_url),
        ).fetchone()
        if existing:
            return redirect_page_with_notice("competitors", "该竞品主页已经添加过了。")
        conn.execute(
            "INSERT INTO competitors(account_id, name, profile_url) VALUES (?, ?, ?)",
            (current["id"], clean_pasted_text(name), clean_profile_url),
        )
    return redirect_page_with_notice("competitors", "竞品主页添加成功。")


@app.post("/crawl/{competitor_id}")
def crawl_competitor(
    competitor_id: int,
    limit: int = Form(20),
    sort_mode: str = Form("latest"),
) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    remaining = get_daily_crawl_remaining(current["id"])
    if remaining <= 0:
        return redirect_page("competitors")
    with connect() as conn:
        competitor = conn.execute(
            "SELECT * FROM competitors WHERE id = ? AND account_id = ?", (competitor_id, current["id"])
        ).fetchone()
    if not competitor:
        return redirect_page("competitors")

    request_limit = max(1, min(limit, DAILY_CRAWL_LIMIT, remaining))
    try:
        notes = crawl_user_notes(
            competitor["profile_url"],
            current["cookie"],
            limit=request_limit,
            sort_mode=sort_mode,
        )
    except Exception as exc:
        mark_account_expired(current["id"])
        return redirect_page_with_error("competitors", f"采集失败：{exc}")
    try:
        stats = save_crawled_notes(notes, current["id"], competitor_id=competitor_id, source="competitor")
    except Exception as exc:
        return redirect_page_with_error("competitors", f"采集结果保存失败：{exc}")
    add_daily_crawl_usage(current["id"], stats["saved"])
    with connect() as conn:
        conn.execute("UPDATE competitors SET last_crawled_at = CURRENT_TIMESTAMP WHERE id = ?", (competitor_id,))
    message = crawl_result_message(stats)
    if stats["saved"] == 0 and stats["skipped"] == 0:
        return redirect_page_with_error("competitors", message)
    return redirect_page_with_notice("competitors", message)


@app.post("/crawl-target")
def crawl_any_target(
    target: str = Form(...),
    limit: int = Form(25),
    keyword_sort: int = Form(0),
) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    remaining = get_daily_crawl_remaining(current["id"])
    if remaining <= 0:
        return redirect_page("competitors")

    request_limit = max(1, min(limit, DAILY_CRAWL_LIMIT, remaining))
    try:
        target_type, notes = crawl_target(
            clean_pasted_text(target),
            current["cookie"],
            limit=request_limit,
            keyword_sort=keyword_sort,
        )
    except Exception as exc:
        mark_account_expired(current["id"])
        return redirect_page_with_error("competitors", f"采集失败：{exc}")
    try:
        stats = save_crawled_notes(notes, current["id"], source=target_type)
    except Exception as exc:
        return redirect_page_with_error("competitors", f"采集结果保存失败：{exc}")
    add_daily_crawl_usage(current["id"], stats["saved"])
    message = crawl_result_message(stats)
    if stats["saved"] == 0 and stats["skipped"] == 0:
        return redirect_page_with_error("competitors", "采集已结束，但没有保存到笔记；可能是关键词无结果、单条链接缺少 xsec_token，或返回内容暂时无法解析。今日额度不会增加。")
    return redirect_page_with_notice("competitors", message)


@app.post("/content/sync")
def sync_content(source_account_ids: list[int] = Form(...)) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()

    selected_ids = [account_id for account_id in dict.fromkeys(source_account_ids) if account_id != current["id"]]
    if not selected_ids:
        return redirect_page_with_error("content", "请选择其他账号作为共享来源。", query_key="content_error")

    copied = 0
    skipped = 0
    synced_names: list[str] = []
    with connect() as conn:
        source_accounts = conn.execute(
            f"""
            SELECT id, name
            FROM accounts
            WHERE id IN ({",".join("?" for _ in selected_ids)})
            ORDER BY id DESC
            """,
            selected_ids,
        ).fetchall()

    if not source_accounts:
        return redirect_page_with_error("content", "没有找到要同步的账号。", query_key="content_error")

    for source_account in source_accounts:
        stats = sync_notes_from_account(source_account["id"], current["id"])
        copied += stats["copied"]
        skipped += stats["skipped"]
        synced_names.append(source_account["name"])

    names = "、".join(synced_names)
    if copied == 0:
        return redirect_page_with_notice(
            "content",
            f"已检查 {names}，当前没有新的内容可同步，跳过 {skipped} 条已有数据。",
            query_key="content_notice",
        )
    return redirect_page_with_notice(
        "content",
        f"已从 {names} 同步 {copied} 条内容，跳过 {skipped} 条重复数据。",
        query_key="content_notice",
    )


@app.post("/copywriting/generate")
def generate_copywriting_result(
    post_topic: str = Form(...),
    post_type: str = Form(...),
    post_goal: str = Form(...),
    word_count: str = Form("默认"),
    core_message: str = Form(""),
    source_note_id: int = Form(0),
    generation_mode: str = Form("normal"),
    differentiation_level: str = Form("medium"),
    reference_title: str = Form(""),
    reference_content: str = Form(""),
) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()

    form_data = {
        "post_topic": post_topic.strip(),
        "post_type": post_type.strip(),
        "post_goal": post_goal.strip(),
        "word_count": word_count.strip() or COPYWRITING_WORD_COUNTS[0],
        "core_message": core_message.strip(),
        "generation_mode": generation_mode.strip() or "normal",
        "differentiation_level": differentiation_level.strip() or "medium",
        "reference_title": reference_title.strip(),
        "reference_content": reference_content.strip(),
    }
    debug_request = {
        "event": "copywriting_route_entered",
        "request_id": f"route-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        "attempt": 0,
        "account_id": current["id"],
        "source_note_id": source_note_id,
        "form_data": form_data,
    }
    log_copywriting_debug(debug_request)
    if not form_data["post_topic"]:
        log_copywriting_debug({**debug_request, "event": "copywriting_route_validation_failed", "error": "请先填写本次想分享的内容。"})
        return redirect_page_with_error("copywriting", "请先填写本次想分享的内容。", query_key="copywriting_error")
    if form_data["post_type"] not in COPYWRITING_TYPES:
        log_copywriting_debug({**debug_request, "event": "copywriting_route_validation_failed", "error": "请选择有效的笔记类型。"})
        return redirect_page_with_error("copywriting", "请选择有效的笔记类型。", query_key="copywriting_error")
    if form_data["post_goal"] not in COPYWRITING_GOALS:
        log_copywriting_debug({**debug_request, "event": "copywriting_route_validation_failed", "error": "请选择有效的发布目标。"})
        return redirect_page_with_error("copywriting", "请选择有效的发布目标。", query_key="copywriting_error")
    if form_data["word_count"] not in COPYWRITING_WORD_COUNTS:
        log_copywriting_debug({**debug_request, "event": "copywriting_route_validation_failed", "error": "请选择有效的字数范围。"})
        return redirect_page_with_error("copywriting", "请选择有效的字数范围。", query_key="copywriting_error")
    if form_data["generation_mode"] not in {"normal", "remix"}:
        log_copywriting_debug({**debug_request, "event": "copywriting_route_validation_failed", "error": "请选择有效的生成模式。"})
        return redirect_page_with_error("copywriting", "请选择有效的生成模式。", query_key="copywriting_error")
    if form_data["differentiation_level"] not in COPYWRITING_DIFFERENTIATION_LEVELS:
        log_copywriting_debug({**debug_request, "event": "copywriting_route_validation_failed", "error": "请选择有效的差异化程度。"})
        return redirect_page_with_error("copywriting", "请选择有效的差异化程度。", query_key="copywriting_error")
    if form_data["generation_mode"] == "remix" and (not form_data["reference_title"] or not form_data["reference_content"]):
        if source_note_id <= 0:
            log_copywriting_debug({**debug_request, "event": "copywriting_route_validation_failed", "error": "缺少复刻参考文案。"})
            return redirect_page_with_error("copywriting", "缺少复刻参考文案。", query_key="copywriting_error")
        with connect() as conn:
            reference_note = conn.execute(
                "SELECT title, body FROM notes WHERE id = ? AND account_id = ? LIMIT 1",
                (source_note_id, current["id"]),
            ).fetchone()
        if not reference_note:
            log_copywriting_debug({**debug_request, "event": "copywriting_route_validation_failed", "error": "没有找到对应的参考笔记。"})
            return redirect_page_with_error("copywriting", "没有找到对应的参考笔记。", query_key="copywriting_error")
        form_data["reference_title"] = str(reference_note.get("title") or "").strip()
        form_data["reference_content"] = strip_note_topics(reference_note.get("body"))

    with connect() as conn:
        brand_profile = conn.execute(
            "SELECT * FROM brand_profiles WHERE account_id = ? LIMIT 1", (current["id"],)
        ).fetchone()
    if not brand_profile:
        log_copywriting_debug({**debug_request, "event": "copywriting_route_validation_failed", "error": "请先完善品牌资料，再生成文案。"})
        return redirect_page_with_error("copywriting", "请先完善品牌资料，再生成文案。", query_key="copywriting_error")

    try:
        result = generate_copywriting(
            api_key=get_app_setting("deepseek_api_key"),
            brand_profile=brand_profile,
            form_data=form_data,
        )
    except requests.HTTPError as exc:
        message = "DeepSeek 请求失败，请检查 API Key、余额或稍后再试。"
        try:
            detail = exc.response.json()
            api_message = str(detail.get("error", {}).get("message") or "").strip()
            if api_message:
                message = f"DeepSeek 请求失败：{api_message[:180]}"
        except Exception:
            pass
        log_copywriting_debug(
            {
                **debug_request,
                "event": "copywriting_route_request_failed",
                "error_type": type(exc).__name__,
                "error": message,
            }
        )
        return redirect_page_with_error("copywriting", message, query_key="copywriting_error")
    except Exception as exc:
        log_copywriting_debug(
            {
                **debug_request,
                "event": "copywriting_route_request_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        return redirect_page_with_error("copywriting", str(exc), query_key="copywriting_error")

    clean_source_note_id = source_note_id if source_note_id > 0 else None
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO copy_generations(
                account_id, source_note_id, generation_mode, differentiation_level, reference_title, reference_content,
                post_topic, post_type, post_goal, word_count, core_message, titles_json, body, tags_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                current["id"],
                clean_source_note_id,
                form_data["generation_mode"],
                form_data["differentiation_level"],
                form_data["reference_title"],
                form_data["reference_content"],
                form_data["post_topic"],
                form_data["post_type"],
                form_data["post_goal"],
                form_data["word_count"],
                form_data["core_message"],
                as_json(result["titles"]),
                result["body"],
                as_json(result["tags"]),
            ),
        )
    return RedirectResponse(
        f"/?page=copywriting&copy_id={cursor.lastrowid}&copywriting_notice={quote('文案已生成。')}",
        status_code=303,
    )


@app.post("/notes/copywriting-remix")
def remix_copywriting_from_note(
    note_id: int = Form(...),
    differentiation_level: str = Form("medium"),
) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    if differentiation_level not in COPYWRITING_DIFFERENTIATION_LEVELS:
        return redirect_page_with_error("content", "请选择有效的差异化程度。", query_key="content_error")
    with connect() as conn:
        note = conn.execute(
            "SELECT id, title, body FROM notes WHERE id = ? AND account_id = ?",
            (note_id, current["id"]),
        ).fetchone()
    if not note:
        return redirect_page("content")
    params = {
        "page": "copywriting",
        "post_topic": str(note.get("title") or "").strip()[:120],
        "post_type": "经验分享",
        "post_goal": "获得收藏",
        "word_count": COPYWRITING_WORD_COUNTS[0],
        "core_message": strip_note_topics(note.get("body"))[:220],
        "source_note_id": str(note["id"]),
        "generation_mode": "remix",
        "differentiation_level": differentiation_level,
        "auto_generate": "1",
    }
    query = "&".join(f"{key}={quote(value)}" for key, value in params.items() if value)
    return RedirectResponse(f"/?{query}", status_code=303)


@app.post("/notes/{note_id}/cover-reference")
def create_cover_reference(note_id: int) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()

    with connect() as conn:
        note = conn.execute(
            "SELECT id, title, image_urls_json FROM notes WHERE id = ? AND account_id = ?",
            (note_id, current["id"]),
        ).fetchone()
        if not note:
            return redirect_page("content")
        image_row = conn.execute(
            """
            SELECT local_path, remote_url
            FROM note_images
            WHERE note_id = ? AND account_id = ?
            ORDER BY image_index
            LIMIT 1
            """,
            (note_id, current["id"]),
        ).fetchone()
        cover_url = ""
        if image_row and image_row["local_path"]:
            local_path = (MEDIA_DIR / image_row["local_path"]).resolve()
            media_root = MEDIA_DIR.resolve()
            if media_root in local_path.parents and local_path.is_file():
                cover_url = "/media/" + str(image_row["local_path"]).replace("\\", "/").lstrip("/")
        if not cover_url and image_row and image_row["remote_url"]:
            cover_url = image_row["remote_url"]
        if not cover_url:
            image_urls = normalize_image_urls(from_json(note.get("image_urls_json"), []))
            cover_url = image_urls[0] if image_urls else ""
        if not cover_url:
            return redirect_page_with_error("content", "这条笔记没有可用封面。", query_key="content_error")
        conn.execute(
            """
            INSERT INTO image_references(account_id, note_id, label, image_url, analysis, status)
            VALUES (?, ?, ?, ?, ?, 'reference')
            """,
            (
                current["id"],
                note["id"],
                f"封面复刻｜{(note.get('title') or '未命名笔记')[:24]}",
                cover_url,
                "从内容看板一键加入的封面参考图，可继续用于封面复刻分析。",
            ),
        )
    return RedirectResponse(
        "/?page=images"
        f"&image_notice={quote('封面已加入图生图参考图 1。')}"
        f"&image_form_mode=remix"
        f"&reference_image_url={quote(cover_url)}"
        f"&reference_image_name={quote('图1｜封面复刻')}",
        status_code=303,
    )


@app.post("/published")
def add_published(title: str = Form(...), body: str = Form(...)) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    with connect() as conn:
        conn.execute(
            "INSERT INTO published_items(account_id, title, body) VALUES (?, ?, ?)",
            (current["id"], title.strip(), body.strip()),
        )
    return redirect_home()


@app.post("/brand-profile")
def save_brand_profile(
    main_theme: str = Form(""),
    audience: str = Form(""),
    tone: str = Form(""),
    product_points: str = Form(""),
    banned_words: str = Form(""),
) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM brand_profiles WHERE account_id = ?", (current["id"],)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE brand_profiles
                SET main_theme = ?, audience = ?, tone = ?, product_points = ?, banned_words = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ?
                """,
                (main_theme.strip(), audience.strip(), tone.strip(), product_points.strip(), banned_words.strip(), current["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO brand_profiles(account_id, main_theme, audience, tone, product_points, banned_words)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (current["id"], main_theme.strip(), audience.strip(), tone.strip(), product_points.strip(), banned_words.strip()),
            )
    return redirect_page_with_notice("brand", "品牌资料已保存。", query_key="brand_notice")


@app.post("/published/import")
def import_published() -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    try:
        notes = fetch_self_published(current["cookie"])
    except Exception:
        notes = []
    with connect() as conn:
        for note in notes:
            title = note.get("title") or note.get("display_title") or "已发布笔记"
            body = note.get("desc") or note.get("content") or ""
            platform_note_id = note.get("id") or note.get("note_id")
            conn.execute(
                "INSERT INTO published_items(account_id, title, body, source, platform_note_id) VALUES (?, ?, ?, 'creator_import', ?)",
                (current["id"], title, body, platform_note_id),
            )
    return redirect_home()


@app.post("/drafts/from-note/{note_id}")
def create_draft(note_id: int, combine_theme: str = Form("")) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    with connect() as conn:
        note = conn.execute("SELECT * FROM notes WHERE id = ? AND account_id = ?", (note_id, current["id"])).fetchone()
        brand_profile = conn.execute(
            "SELECT * FROM brand_profiles WHERE account_id = ? LIMIT 1", (current["id"],)
        ).fetchone()
        references = conn.execute(
            """
            SELECT body FROM published_items WHERE account_id = ?
            UNION ALL
            SELECT body FROM drafts WHERE account_id = ? AND status IN ('approved', 'published')
            """,
            (current["id"], current["id"]),
        ).fetchall()
    if not note:
        return redirect_home()

    title_prefix = f"{combine_theme.strip()}｜" if combine_theme.strip() else ""
    title = f"{title_prefix}{note['title']}".strip("｜")
    body = build_placeholder_draft(note["title"] or "", note["body"] or "", combine_theme.strip(), brand_profile)
    duplicate_segments = exact_duplicate_segments(body, [row["body"] for row in references], min_len=12)

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO drafts(account_id, source_note_id, combine_theme, title, body, duplicate_segments_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (current["id"], note_id, combine_theme.strip(), title, body, as_json(duplicate_segments)),
        )
    return redirect_home()


def build_placeholder_draft(title: str, body: str, combine_theme: str, brand_profile: dict | None = None) -> str:
    theme_line = f"结合主题：{combine_theme}\n" if combine_theme else ""
    brand_lines = []
    if brand_profile:
        fields = [
            ("账号主要主题", brand_profile.get("main_theme")),
            ("目标人群", brand_profile.get("audience")),
            ("表达风格", brand_profile.get("tone")),
            ("产品/服务卖点", brand_profile.get("product_points")),
            ("禁用词/表达", brand_profile.get("banned_words")),
        ]
        brand_lines = [f"- {label}：{value.strip()}" for label, value in fields if value and value.strip()]
    brand_context = "品牌资料约束：\n" + "\n".join(brand_lines) + "\n\n" if brand_lines else ""
    return (
        f"{theme_line}"
        f"{brand_context}"
        f"参考选题：{title}\n\n"
        "重新生成思路：\n"
        "1. 保留选题策略，不复用原文表达。\n"
        "2. 换成自己的产品、经历、案例或观点。\n"
        "3. 开头先讲目标人群的具体问题。\n\n"
        "草稿正文：\n"
        f"{body[:160]}\n\n"
        "这里后续会接入 LLM API，按你的品牌资料和可选结合主题生成完整原创内容。"
    )


@app.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: int) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    with connect() as conn:
        draft = conn.execute("SELECT * FROM drafts WHERE id = ? AND account_id = ?", (draft_id, current["id"])).fetchone()
        if draft:
            conn.execute("UPDATE drafts SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (draft_id,))
            conn.execute(
                "INSERT INTO published_items(account_id, title, body, source) VALUES (?, ?, ?, 'approved_draft')",
                (current["id"], draft["title"], draft["body"]),
            )
    return redirect_home()


def run_note_scoring(note_id: int, account_id: int) -> None:
    with connect() as conn:
        note = conn.execute(
            "SELECT * FROM notes WHERE id = ? AND account_id = ?",
            (note_id, account_id),
        ).fetchone()
        if not note:
            return
        cover_url = first_cover_url(conn, note)
        note["publication_age_days"] = publication_age_days(note.get("published_at"))

    try:
        result = score_note_with_model(
            note,
            cover_url,
            api_token=get_app_setting("kie_api_key"),
            proxy=get_kie_proxy_value(),
        )
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                """
                UPDATE notes
                SET scoring_status = 'failed',
                    scoring_error = ?
                WHERE id = ? AND account_id = ?
                """,
                (str(exc)[:500], note_id, account_id),
            )
        return

    score = max(0, min(100, int(result["爆款信号"])))
    summary = f"爆款概率：{result.get('爆款概率', '未知')}；爆款信号：{score}"
    with connect() as conn:
        conn.execute(
            """
            UPDATE notes
            SET score = ?,
                summary = ?,
                ai_score_json = ?,
                scoring_status = 'completed',
                scoring_error = NULL
            WHERE id = ? AND account_id = ?
            """,
            (score, summary, as_json(result), note_id, account_id),
        )


@app.post("/notes/{note_id}/score")
def score_note_ai(note_id: int, background_tasks: BackgroundTasks) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    with connect() as conn:
        note = conn.execute(
            "SELECT id, scoring_status FROM notes WHERE id = ? AND account_id = ?",
            (note_id, current["id"]),
        ).fetchone()
        if not note:
            return redirect_page("content")
        if note.get("scoring_status") == "scoring":
            return redirect_page_with_notice("content", "这条笔记正在打分。", query_key="content_notice")
        conn.execute(
            """
            UPDATE notes
            SET scoring_status = 'scoring',
                scoring_error = NULL,
                scoring_started_at = CURRENT_TIMESTAMP
            WHERE id = ? AND account_id = ?
            """,
            (note_id, current["id"]),
        )
    background_tasks.add_task(run_note_scoring, note_id, current["id"])
    return redirect_page_with_notice("content", "已开始打分，可继续浏览其他页面。", query_key="content_notice")


@app.post("/notes/{note_id}/hide")
def hide_note(note_id: int) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    with connect() as conn:
        delete_note_images(conn, current["id"], note_id)
        conn.execute(
            """
            UPDATE notes
            SET is_hidden = 1, image_urls_json = '[]'
            WHERE id = ? AND account_id = ?
            """,
            (note_id, current["id"]),
        )
    return redirect_page("content")


@app.post("/image-references", response_model=None)
def add_image_reference(
    request: Request,
    background_tasks: BackgroundTasks,
    label: str = Form(""),
    image_url: str = Form(""),
    analysis: str = Form(""),
    prompt: str = Form(""),
    output_target: str = Form(""),
    aspect_ratio: str = Form("9:16"),
    resolution: str = Form("1K"),
    image_model: str = Form("gpt-image-2-text-to-image"),
    image_mode: str = Form("prompt"),
    reference_images: list[UploadFile] | None = File(None),
):
    current = get_current_account()
    if not current:
        return redirect_home()
    reference_uploads = [upload for upload in (reference_images or [])[:4] if upload.filename]
    reference_upload_payloads: list[dict[str, object]] = []
    local_reference_urls: list[str] = []
    for upload in reference_uploads:
        upload.file.seek(0)
        content = upload.file.read()
        if not content:
            continue
        content_type = upload.content_type or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream"
        saved_url = save_reference_bytes(current["id"], upload.filename, content_type, content)
        if saved_url:
            local_reference_urls.append(saved_url)
        reference_upload_payloads.append(
            {
                "filename": upload.filename,
                "content_type": content_type,
                "content": content,
            }
        )
    cleaned_image_url = image_url.strip()
    cleaned_prompt = prompt.strip()
    cleaned_target = output_target.strip()
    cleaned_label = label.strip()
    cleaned_analysis = analysis.strip()
    cleaned_mode = image_mode.strip() or "prompt"
    cleaned_model = image_model.strip() if image_model.strip() in IMAGE_MODEL_OPTIONS else "gpt-image-2-text-to-image"
    kie_task_id = ""
    final_prompt = cleaned_prompt
    is_prompt_mode = cleaned_mode == "prompt"
    if cleaned_mode == "prompt":
        if not cleaned_prompt:
            return redirect_page_with_error("images", "请先填写 Prompt。", query_key="image_error")
        final_prompt = build_image_generation_prompt(cleaned_target, cleaned_prompt)
        api_key = get_app_setting("kie_api_key")
        if not api_key:
            return redirect_page_with_error("images", "请先在设置里配置 KIE API Key。", query_key="image_error")
        cleaned_label = cleaned_label or f"文生图｜{(cleaned_target or '未命名任务')[:18]}"
        cleaned_analysis = " | ".join(
            part
            for part in [
                f"Prompt：{cleaned_prompt[:120]}",
                f"输出目标：{cleaned_target or '未设置'}",
                f"图像规格：{aspect_ratio.strip() or '9:16'}",
                f"分辨率：{resolution.strip() or '1K'}",
            ]
            if part
        )
    elif cleaned_mode == "remix":
        if not cleaned_analysis:
            return redirect_page_with_error("images", "请先填写图生图 Prompt。", query_key="image_error")
        final_prompt = build_image_generation_prompt(cleaned_target, cleaned_analysis)
        api_key = get_app_setting("kie_api_key")
        if not api_key:
            return redirect_page_with_error("images", "请先在设置里配置 KIE API Key。", query_key="image_error")
        input_urls = [cleaned_image_url] if cleaned_image_url else []
        if not reference_upload_payloads and not input_urls:
            return redirect_page_with_error("images", "请先上传至少 1 张参考图。", query_key="image_error")
        cleaned_label = cleaned_label or "图生图任务"
        cleaned_prompt = cleaned_analysis
        cleaned_image_url = cleaned_image_url if not reference_upload_payloads else ""
    task_id = next(IMAGE_TASK_COUNTER)
    task = {
        "id": task_id,
        "mode": "prompt" if is_prompt_mode else "remix",
        "tab_label": "",
        "prompt": cleaned_prompt or cleaned_analysis or cleaned_label or "图生图任务",
        "final_prompt": final_prompt,
        "output_target": cleaned_target or ("图生图" if cleaned_mode == "remix" else "小红书封面"),
        "aspect_ratio": aspect_ratio.strip() or ("参考原图" if cleaned_mode == "remix" else "9:16"),
        "resolution": resolution.strip() or ("跟随结果" if cleaned_mode == "remix" else "1K"),
        "image_model": cleaned_model,
        "image_model_label": IMAGE_MODEL_OPTIONS[cleaned_model]["label"],
        "image_url": cleaned_image_url,
        "reference_urls": local_reference_urls,
        "status": "loading",
        "backend_status": "queued",
        "backend_task_id": kie_task_id,
        "progress": "",
        "error": "",
        "started_at": time.time(),
        "eta_seconds": 120,
        "is_selected": True,
    }
    with IMAGE_TASK_LOCK:
        IMAGE_RUNTIME_TASKS.setdefault(current["id"], []).append(task)
    background_tasks.add_task(
        prepare_and_poll_kie_image_task,
        current["id"],
        task_id,
        "prompt" if is_prompt_mode else "remix",
        api_key,
        final_prompt,
        aspect_ratio.strip() or "auto",
        resolution.strip() or "1K",
        cleaned_model,
        reference_upload_payloads,
        cleaned_image_url,
    )
    notice = "文生图任务已提交，后台正在生成。" if is_prompt_mode else "图生图任务已提交，后台正在生成。"
    if "application/json" in str(request.headers.get("accept", "")):
        prompt_tasks, remix_tasks = split_image_tasks(current["id"], task_id)
        task_list = prompt_tasks if is_prompt_mode else remix_tasks
        response_task = next((item for item in task_list if int(item.get("id", 0)) == task_id), task)
        return JSONResponse(
            {
                "ok": True,
                "notice": notice,
                "task": response_task,
            }
        )
    return RedirectResponse(
        f"/?page=images&task_id={task_id}&image_notice={quote(notice)}",
        status_code=303,
    )


@app.post("/video-migration/step1", response_model=None)
def create_video_migration_step1_task(
    request: Request,
    background_tasks: BackgroundTasks,
    source_image: UploadFile = File(...),
    face_image: UploadFile = File(...),
):
    current = get_current_account()
    if not current:
        return JSONResponse({"ok": False, "error": "请先登录账号。"}, status_code=401)
    api_key = get_app_setting("kie_api_key")
    if not api_key:
        return JSONResponse({"ok": False, "error": "请先在设置里配置 KIE API Key。"}, status_code=400)
    if not source_image.filename or not face_image.filename:
        return JSONResponse({"ok": False, "error": "请先上传图 1 和图 2。"}, status_code=400)

    source_image.file.seek(0)
    face_image.file.seek(0)
    source_content = source_image.file.read()
    face_content = face_image.file.read()
    if not source_content or not face_content:
        return JSONResponse({"ok": False, "error": "图片文件不能为空。"}, status_code=400)

    source_content_type = source_image.content_type or mimetypes.guess_type(source_image.filename)[0] or "application/octet-stream"
    face_content_type = face_image.content_type or mimetypes.guess_type(face_image.filename)[0] or "application/octet-stream"
    source_local_url = save_reference_bytes(current["id"], source_image.filename, source_content_type, source_content)
    face_local_url = save_reference_bytes(current["id"], face_image.filename, face_content_type, face_content)

    task_id = next(VIDEO_MIGRATION_TASK_COUNTER)
    display_index = next_video_migration_display_index(current["id"])
    task = {
        "id": task_id,
        "display_index": display_index,
        "tab_label": "",
        "stage": "step1",
        "prompt": "把图1的脸换成图2",
        "final_prompt": "把图1的脸换成图2。保持图1的人物姿态、构图、服装、背景、光线和画面风格，只替换脸部身份。输出竖版 9:16 图片。",
        "source_image_url": source_local_url or "",
        "face_image_url": face_local_url or "",
        "result_image_url": "",
        "status": "loading",
        "backend_status": "queued",
        "backend_task_id": "",
        "video_status": "idle",
        "runninghub_status": "",
        "runninghub_task_id": "",
        "result_video_url": "",
        "target_video_name": "",
        "target_video_duration": "",
        "estimated_video_seconds": "",
        "video_started_at": "",
        "progress": "",
        "error": "",
        "started_at": time.time(),
        "eta_seconds": 120,
        "is_selected": True,
    }
    with VIDEO_MIGRATION_TASK_LOCK:
        VIDEO_MIGRATION_RUNTIME_TASKS.setdefault(current["id"], []).append(task)

    background_tasks.add_task(
        prepare_and_poll_video_migration_step1,
        current["id"],
        task_id,
        api_key,
        {
            "filename": source_image.filename,
            "content_type": source_content_type,
            "content": source_content,
        },
        {
            "filename": face_image.filename,
            "content_type": face_content_type,
            "content": face_content,
        },
    )
    response_task = next((item for item in split_video_migration_tasks(current["id"], task_id) if int(item.get("id", 0)) == task_id), task)
    return JSONResponse({"ok": True, "notice": "首帧任务已提交，正在调用 KIE 香蕉-2。", "task": response_task})


@app.post("/video-migration/step2", response_model=None)
def create_video_migration_step2_task(
    request: Request,
    background_tasks: BackgroundTasks,
    task_id: int = Form(...),
    target_video: UploadFile = File(...),
    video_duration_seconds: str = Form(""),
):
    current = get_current_account()
    if not current:
        return JSONResponse({"ok": False, "error": "请先登录账号。"}, status_code=401)
    api_key = get_runninghub_api_key()
    if not api_key:
        return JSONResponse({"ok": False, "error": "请先在设置里配置 RunningHub API Key。"}, status_code=400)
    task = find_video_migration_task(current["id"], task_id)
    if not task:
        return JSONResponse({"ok": False, "error": "任务不存在或已删除。"}, status_code=404)
    if str(task.get("status") or "") != "success" or not str(task.get("result_image_url") or "").strip():
        return JSONResponse({"ok": False, "error": "请等待第一步首帧图生成完成。"}, status_code=400)
    if not target_video.filename:
        return JSONResponse({"ok": False, "error": "请上传目标视频。"}, status_code=400)

    target_video.file.seek(0)
    video_content = target_video.file.read()
    if not video_content:
        return JSONResponse({"ok": False, "error": "目标视频文件不能为空。"}, status_code=400)
    if len(video_content) > RUNNINGHUB_MAX_UPLOAD_BYTES:
        return JSONResponse({"ok": False, "error": "目标视频文件过大，请压缩后再试。"}, status_code=400)

    try:
        duration = max(0.0, float(video_duration_seconds or "0"))
    except ValueError:
        duration = 0.0
    if duration > 20.5:
        return JSONResponse({"ok": False, "error": "目标视频最长支持 20 秒。"}, status_code=400)
    estimated_seconds = int(max(90, duration * 90)) if duration else 0
    content_type = target_video.content_type or mimetypes.guess_type(target_video.filename)[0] or "video/mp4"
    update_video_migration_task(
        current["id"],
        task_id,
        stage="step2",
        video_status="loading",
        runninghub_status="queued",
        target_video_name=target_video.filename,
        target_video_duration=round(duration, 2) if duration else "",
        estimated_video_seconds=estimated_seconds,
        video_started_at=time.time(),
        result_video_url="",
        error="",
    )
    background_tasks.add_task(
        prepare_and_poll_runninghub_video_task,
        current["id"],
        task_id,
        api_key,
        {
            "filename": target_video.filename,
            "content_type": content_type,
            "content": video_content,
        },
    )
    response_task = next((item for item in split_video_migration_tasks(current["id"], task_id) if int(item.get("id", 0)) == task_id), task)
    return JSONResponse({"ok": True, "notice": "视频迁移任务已提交，RunningHub 正在生成。", "task": response_task})


@app.post("/image-tasks/{task_id}/delete", response_model=None)
def delete_image_task(request: Request, task_id: int):
    current = get_current_account()
    if not current:
        return redirect_home()
    with IMAGE_TASK_LOCK:
        tasks = IMAGE_RUNTIME_TASKS.get(current["id"], [])
        IMAGE_RUNTIME_TASKS[current["id"]] = [task for task in tasks if int(task.get("id", 0)) != task_id]
    if "application/json" in str(request.headers.get("accept", "")):
        return JSONResponse({"ok": True, "task_id": task_id})
    return redirect_page("images")


@app.get("/video-migration/tasks")
def video_migration_tasks_snapshot() -> dict[str, object]:
    current = get_current_account()
    if not current:
        return {"tasks": [], "count": 0}
    tasks = split_video_migration_tasks(current["id"])
    return {
        "tasks": tasks,
        "count": len(tasks),
    }


@app.post("/video-migration/tasks/{task_id}/delete", response_model=None)
def delete_video_migration_task(request: Request, task_id: int):
    current = get_current_account()
    if not current:
        return redirect_home()
    with VIDEO_MIGRATION_TASK_LOCK:
        tasks = VIDEO_MIGRATION_RUNTIME_TASKS.get(current["id"], [])
        VIDEO_MIGRATION_RUNTIME_TASKS[current["id"]] = [task for task in tasks if int(task.get("id", 0)) != task_id]
    if "application/json" in str(request.headers.get("accept", "")):
        return JSONResponse({"ok": True, "task_id": task_id})
    return redirect_page("video_migration")


@app.get("/image-proxy")
def proxy_image(url: str) -> Response:
    cleaned_url = str(url or "").strip()
    if not cleaned_url:
        return Response("missing url", status_code=400)
    parsed = urlparse(cleaned_url)
    if parsed.scheme not in {"http", "https"}:
        return Response("unsupported url", status_code=400)
    response = requests.get(
        cleaned_url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type") or mimetypes.guess_type(parsed.path)[0] or "image/png"
    if not content_type.startswith("image/"):
        return Response("not an image", status_code=415)
    return Response(
        content=response.content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.get("/image-tasks")
def image_tasks_snapshot() -> dict[str, object]:
    current = get_current_account()
    if not current:
        return {"prompt": [], "remix": []}
    prompt_tasks, remix_tasks = split_image_tasks(current["id"])
    return {
        "prompt": prompt_tasks,
        "remix": remix_tasks,
        "count": len(prompt_tasks) + len(remix_tasks),
    }
