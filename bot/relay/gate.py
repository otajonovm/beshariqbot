"""Qaysi chatdan yo'naltirish mumkin — sozlama va xabar turi bo'yicha, tarmoqsiz."""

from __future__ import annotations


def rejection_reason(
    *,
    chat_type: str,
    chat_id: int,
    main_id: int,
    source_ids: list[int] | None,
    is_bot: bool,
    text: str,
    edited: bool,
) -> str | None:
    """None bo'lsa, matn klassifikatsiyasiga o'tish mumkin."""
    if edited:
        return "edited"
    if is_bot:
        return "bot"
    if chat_type not in {"group", "supergroup"}:
        return "not-group"
    if chat_id == main_id:
        return "main-group"
    if source_ids is not None and chat_id not in source_ids:
        return "not-source"
    stripped = text.lstrip()
    if not stripped:
        return "empty"
    if stripped.startswith("/"):
        return "command"
    return None
