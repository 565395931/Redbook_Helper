from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from app.db import as_json, connect, from_json, migrate
from app.services.crawler import crawl_target, crawl_user_notes, fetch_self_published
from app.services.dedupe import exact_duplicate_segments
from app.services.scoring import score_note

BASE_DIR = Path(__file__).resolve().parent
DAILY_CRAWL_LIMIT = 25

app = FastAPI(title="Redbook Analisyze")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def render_topics(value: str | None) -> Markup:
    text = str(value or "")
    pieces: list[str] = []
    last_end = 0
    pattern = re.compile(r"#([^#\r\n]+?)\[话题\]#")
    for match in pattern.finditer(text):
        pieces.append(str(escape(text[last_end : match.start()])))
        topic = str(escape(match.group(1).strip()))
        pieces.append(f'<span class="topic-tag">{topic}</span>')
        last_end = match.end()
    pieces.append(str(escape(text[last_end:])))
    html = "".join(pieces).replace("#", "").replace("[话题]", "")
    return Markup(html)


templates.env.filters["render_topics"] = render_topics


@app.on_event("startup")
def startup() -> None:
    migrate()


def redirect_home() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


def redirect_page(page: str) -> RedirectResponse:
    return RedirectResponse(f"/?page={page}", status_code=303)


def redirect_page_with_error(page: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"/?page={page}&crawl_error={quote(message[:240])}", status_code=303)


def redirect_page_with_notice(page: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"/?page={page}&crawl_notice={quote(message[:240])}", status_code=303)


def get_current_account() -> dict | None:
    with connect() as conn:
        account = conn.execute("SELECT * FROM accounts WHERE is_current = 1 LIMIT 1").fetchone()
        if account:
            return account
        account = conn.execute("SELECT * FROM accounts ORDER BY id LIMIT 1").fetchone()
        if account:
            conn.execute("UPDATE accounts SET is_current = CASE WHEN id = ? THEN 1 ELSE 0 END", (account["id"],))
        return account


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


def save_crawled_notes(
    notes: list[dict],
    account_id: int,
    competitor_id: int | None = None,
    source: str = "crawl",
) -> dict[str, int]:
    stats = {"received": len(notes), "saved": 0, "skipped": 0}
    with connect() as conn:
        for note in notes:
            note_id, note_url = _note_identity(note)
            if not note_id and not note_url:
                stats["skipped"] += 1
                continue
            if _note_exists(conn, account_id, note_id, note_url):
                stats["skipped"] += 1
                continue

            score, summary = score_note(
                note.get("title", ""),
                note.get("desc", ""),
                int(note.get("liked_count") or 0),
                int(note.get("collected_count") or 0),
                int(note.get("comment_count") or 0),
            )
            conn.execute(
                """
                INSERT INTO notes(
                    account_id, competitor_id, source, platform_note_id, note_url, note_type,
                    author_name, title, body, tags_json, image_urls_json, like_count, collect_count,
                    comment_count, share_count, score, summary, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    as_json(note.get("image_list", [])),
                    int(note.get("liked_count") or 0),
                    int(note.get("collected_count") or 0),
                    int(note.get("comment_count") or 0),
                    int(note.get("share_count") or 0),
                    score,
                    summary,
                    note.get("raw_json", "{}"),
                ),
            )
            stats["saved"] += 1
    return stats


def crawl_result_message(stats: dict[str, int]) -> str:
    saved = int(stats.get("saved", 0))
    skipped = int(stats.get("skipped", 0))
    received = int(stats.get("received", 0))
    if saved:
        return f"采集完成：新增 {saved} 条，跳过 {skipped} 条已爬过；本轮返回 {received} 条。今日额度增加 {saved}。"
    if skipped:
        return f"本轮没有新增：{skipped} 条都已爬过；本轮返回 {received} 条。今日额度不增加。"
    return "采集已结束，但没有保存到笔记；可能是链接/Cookie可用但接口没有返回可解析内容。今日额度不会增加。"


@app.get("/", response_class=HTMLResponse)
def home(request: Request, page: str = "accounts") -> HTMLResponse:
    current = get_current_account()
    valid_pages = {"accounts", "brand", "competitors", "content", "images", "review"}
    current_page = page if page in valid_pages else "accounts"
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
        if current:
            brand_profile = conn.execute(
                "SELECT * FROM brand_profiles WHERE account_id = ? LIMIT 1", (current["id"],)
            ).fetchone()
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

    for draft in drafts:
        draft["duplicate_segments"] = from_json(draft["duplicate_segments_json"], [])
    for note in notes:
        note["image_urls"] = from_json(note["image_urls_json"], [])
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
            "daily_crawl_limit": DAILY_CRAWL_LIMIT,
            "daily_crawl_used": daily_crawl_used,
            "daily_crawl_remaining": daily_crawl_remaining,
        },
    )


@app.post("/accounts")
def create_account(name: str = Form(...), phone: str = Form(""), cookie: str = Form(...)) -> RedirectResponse:
    with connect() as conn:
        has_accounts = conn.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()["count"] > 0
        conn.execute(
            """
            INSERT INTO accounts(name, phone, cookie, login_method, login_status, is_current)
            VALUES (?, ?, ?, 'manual_cookie', 'logged_in', ?)
            """,
            (name.strip(), phone.strip(), cookie.strip(), 0 if has_accounts else 1),
        )
    return redirect_page("brand")


@app.post("/accounts/switch")
def switch_account(account_id: int = Form(...)) -> RedirectResponse:
    with connect() as conn:
        conn.execute("UPDATE accounts SET is_current = CASE WHEN id = ? THEN 1 ELSE 0 END", (account_id,))
    return redirect_home()


@app.post("/accounts/update-cookie")
def update_account_cookie(account_id: int = Form(...), cookie: str = Form(...)) -> RedirectResponse:
    with connect() as conn:
        conn.execute(
            """
            UPDATE accounts
            SET cookie = ?, login_status = 'logged_in', login_method = 'manual_cookie',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (cookie.strip(), account_id),
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
    with connect() as conn:
        conn.execute(
            "INSERT INTO competitors(account_id, name, profile_url) VALUES (?, ?, ?)",
            (current["id"], name.strip(), profile_url.strip()),
        )
    return redirect_home()


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
    stats = save_crawled_notes(notes, current["id"], competitor_id=competitor_id, source="competitor")
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
            target.strip(),
            current["cookie"],
            limit=request_limit,
            keyword_sort=keyword_sort,
        )
    except Exception as exc:
        mark_account_expired(current["id"])
        return redirect_page_with_error("competitors", f"采集失败：{exc}")
    stats = save_crawled_notes(notes, current["id"], source=target_type)
    add_daily_crawl_usage(current["id"], stats["saved"])
    message = crawl_result_message(stats)
    if stats["saved"] == 0 and stats["skipped"] == 0:
        return redirect_page_with_error("competitors", "采集已结束，但没有保存到笔记；可能是关键词无结果、单条链接缺少 xsec_token，或返回内容暂时无法解析。今日额度不会增加。")
    return redirect_page_with_notice("competitors", message)


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
    return redirect_home()


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


@app.post("/notes/{note_id}/hide")
def hide_note(note_id: int) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    with connect() as conn:
        conn.execute(
            """
            UPDATE notes
            SET is_hidden = 1, image_urls_json = '[]'
            WHERE id = ? AND account_id = ?
            """,
            (note_id, current["id"]),
        )
    return redirect_page("content")


@app.post("/image-references")
def add_image_reference(label: str = Form(""), image_url: str = Form(""), analysis: str = Form("")) -> RedirectResponse:
    current = get_current_account()
    if not current:
        return redirect_home()
    with connect() as conn:
        conn.execute(
            "INSERT INTO image_references(account_id, label, image_url, analysis) VALUES (?, ?, ?, ?)",
            (current["id"], label.strip(), image_url.strip(), analysis.strip()),
        )
    return redirect_home()
