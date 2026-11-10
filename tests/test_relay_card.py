from __future__ import annotations

from bot.relay.card import RelayCard, contact_url, message_url, render


def test_private_supergroup_links() -> None:
    text, markup = render(
        RelayCard(
            user_id=5,
            full_name="<Ali>",
            username="Ali_User",
            chat_id=-100123,
            chat_title="A&B",
            chat_username=None,
            message_id=9,
            body="taksi <kerak>",
            phones=["+998901112233"],
        )
    )
    assert '<a href="tg://user?id=5">&lt;Ali&gt;</a>' in text
    assert "(@Ali_User)" in text
    assert "<code>+998901112233</code>" in text
    assert "A&amp;B" in text
    assert "taksi &lt;kerak&gt;" in text
    buttons = [button for row in markup.inline_keyboard for button in row]
    urls = [button.url for button in buttons]
    assert "https://t.me/Ali_User" in urls
    assert "https://t.me/c/123/9" in urls
    contact = next(b for b in buttons if b.url == "https://t.me/Ali_User")
    assert contact.style == "success"


def test_public_group_and_hidden_username() -> None:
    assert message_url(-100555, 4, "taxi_chat") == "https://t.me/taxi_chat/4"
    assert contact_url(9, None) == "tg://user?id=9"
    text, markup = render(
        RelayCard(
            user_id=9,
            full_name="",
            username=None,
            chat_id=-100555,
            chat_title="Ommaviy",
            chat_username="taxi_chat",
            message_id=4,
            body="ketaman",
            phones=[],
        )
    )
    assert "Yo'lovchi" in text
    assert "📞" not in text
    urls = [button.url for row in markup.inline_keyboard for button in row]
    assert "tg://user?id=9" in urls
    assert "https://t.me/taxi_chat/4" in urls
