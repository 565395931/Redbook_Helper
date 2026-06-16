from __future__ import annotations

def exact_duplicate_segments(candidate: str, references: list[str], min_len: int = 12) -> list[str]:
    """Find exact shared text spans. This intentionally avoids fuzzy matching."""
    candidate = candidate or ""
    if len(candidate) < min_len:
        return []

    segments: set[str] = set()
    for reference in references:
        reference = reference or ""
        if len(reference) < min_len:
            continue
        for size in range(min(80, len(candidate)), min_len - 1, -1):
            for start in range(0, len(candidate) - size + 1):
                piece = candidate[start : start + size]
                if piece.strip() and piece in reference:
                    segments.add(piece)

    return sorted(_remove_contained_segments(segments), key=len, reverse=True)


def _remove_contained_segments(segments: set[str]) -> list[str]:
    ordered = sorted(segments, key=len, reverse=True)
    kept: list[str] = []
    for segment in ordered:
        if not any(segment in existing for existing in kept):
            kept.append(segment)
    return kept
