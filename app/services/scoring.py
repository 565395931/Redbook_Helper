from __future__ import annotations


def score_note(title: str, body: str, like_count: int, collect_count: int, comment_count: int) -> tuple[int, str]:
    text = f"{title}\n{body}".strip()
    length_score = min(35, len(text) // 12)
    engagement_score = min(45, like_count // 20 + collect_count // 15 + comment_count // 10)
    structure_score = 10 if any(mark in text for mark in ["1.", "1、", "第一", "首先", "最后"]) else 4
    hook_score = 10 if len(title) >= 8 else 4
    score = min(100, length_score + engagement_score + structure_score + hook_score)
    summary = f"互动分 {engagement_score}，结构分 {structure_score}，标题吸引力 {hook_score}，长度分 {length_score}。"
    return score, summary
