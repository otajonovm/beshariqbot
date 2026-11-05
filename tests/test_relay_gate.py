from __future__ import annotations

from bot.relay.gate import rejection_reason


def test_main_group_is_not_a_source() -> None:
    reason = rejection_reason(
        chat_type="supergroup",
        chat_id=-100,
        main_id=-100,
        source_ids=None,
        is_bot=False,
        text="taksi kerak",
        edited=False,
    )
    assert reason == "main-group"


def test_empty_source_list_accepts_other_groups() -> None:
    reason = rejection_reason(
        chat_type="supergroup",
        chat_id=-200,
        main_id=-100,
        source_ids=None,
        is_bot=False,
        text="taksi kerak",
        edited=False,
    )
    assert reason is None


def test_explicit_sources_and_guards() -> None:
    assert (
        rejection_reason(
            chat_type="supergroup",
            chat_id=-300,
            main_id=-100,
            source_ids=[-200],
            is_bot=False,
            text="taksi kerak",
            edited=False,
        )
        == "not-source"
    )
    assert (
        rejection_reason(
            chat_type="private",
            chat_id=1,
            main_id=-100,
            source_ids=None,
            is_bot=False,
            text="taksi kerak",
            edited=False,
        )
        == "not-group"
    )
    assert (
        rejection_reason(
            chat_type="supergroup",
            chat_id=-200,
            main_id=-100,
            source_ids=None,
            is_bot=True,
            text="taksi kerak",
            edited=False,
        )
        == "bot"
    )
    assert (
        rejection_reason(
            chat_type="supergroup",
            chat_id=-200,
            main_id=-100,
            source_ids=None,
            is_bot=False,
            text="taksi kerak",
            edited=True,
        )
        == "edited"
    )
    assert (
        rejection_reason(
            chat_type="group",
            chat_id=-200,
            main_id=-100,
            source_ids=None,
            is_bot=False,
            text="/top",
            edited=False,
        )
        == "command"
    )
