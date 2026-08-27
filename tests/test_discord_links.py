from bot.cogs.files import (
    DISCORD_BUTTON_URL_MAX,
    DISCORD_EMBED_FIELD_MAX,
    DISCORD_MESSAGE_CONTENT_MAX,
    LinkView,
    can_use_button_url,
    embed_link_value,
    link_text_file,
    message_link_content,
)


def make_url(length: int) -> str:
    prefix = "https://example.com/"
    assert length >= len(prefix)
    return prefix + ("a" * (length - len(prefix)))


def test_button_accepts_512_and_rejects_513() -> None:
    assert can_use_button_url(make_url(DISCORD_BUTTON_URL_MAX)) is True
    assert can_use_button_url(make_url(DISCORD_BUTTON_URL_MAX + 1)) is False


def test_long_signed_url_is_not_added_as_button() -> None:
    long_url = make_url(DISCORD_BUTTON_URL_MAX + 100)
    preview = "https://file-host.base44.app/file/abc123"
    view = LinkView(long_url, preview)

    assert len(view.children) == 1
    assert view.children[0].label == "Preview"


def test_embed_field_fallback_after_1024_chars() -> None:
    normal = make_url(DISCORD_EMBED_FIELD_MAX)
    long_url = make_url(DISCORD_EMBED_FIELD_MAX + 1)

    assert embed_link_value(normal) == normal
    assert "muito longo" in embed_link_value(long_url)


def test_long_url_moves_to_message_content() -> None:
    url = make_url(DISCORD_EMBED_FIELD_MAX + 1)
    assert message_link_content(url) == url
    assert link_text_file(url) is None


def test_extreme_url_uses_text_attachment() -> None:
    url = make_url(DISCORD_MESSAGE_CONTENT_MAX + 1)
    assert "link.txt" in (message_link_content(url) or "")

    file = link_text_file(url)
    assert file is not None
    assert file.filename == "link.txt"
    file.close()
