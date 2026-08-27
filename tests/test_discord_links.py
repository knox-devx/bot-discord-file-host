from bot.cogs.files import (
    DISCORD_BUTTON_URL_MAX,
    DISCORD_MESSAGE_CONTENT_MAX,
    LinkView,
    RevealLinkButton,
    can_use_button_url,
    link_message_content,
    make_link_attachment,
)


def make_url(length: int) -> str:
    prefix = "https://example.com/"
    assert length >= len(prefix)
    return prefix + ("a" * (length - len(prefix)))


def test_button_accepts_512_and_rejects_513() -> None:
    assert can_use_button_url(make_url(DISCORD_BUTTON_URL_MAX)) is True
    assert can_use_button_url(make_url(DISCORD_BUTTON_URL_MAX + 1)) is False


def test_normal_link_uses_url_button() -> None:
    url = "https://file-host.base44.app/s/abc123"
    view = LinkView(owner_id=123, full_url=url, button_url=url)
    assert len(view.children) == 1
    assert view.children[0].label == "Abrir arquivo"


def test_long_link_without_shortener_uses_private_reveal_button() -> None:
    long_url = make_url(DISCORD_BUTTON_URL_MAX + 100)
    preview = "https://file-host.base44.app/file/abc123"
    view = LinkView(
        owner_id=123,
        full_url=long_url,
        button_url=None,
        view_url=preview,
    )

    assert len(view.children) == 2
    assert isinstance(view.children[0], RevealLinkButton)
    assert view.children[0].label == "Receber link"
    assert view.children[1].label == "Preview"


def test_link_fits_normal_message() -> None:
    url = make_url(DISCORD_MESSAGE_CONTENT_MAX)
    assert link_message_content(url) == url
    assert make_link_attachment(url) is None


def test_extreme_link_uses_text_attachment() -> None:
    url = make_url(DISCORD_MESSAGE_CONTENT_MAX + 1)
    assert "link.txt" in link_message_content(url)

    file = make_link_attachment(url)
    assert file is not None
    assert file.filename == "link.txt"
    file.close()
