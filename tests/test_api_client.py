from bot.api_client import (
    MAX_SIGNED_URL_SECONDS,
    FileHostError,
    absolute_view_url,
    build_signed_request_payload,
    parse_shorten_payload,
    parse_signed_payload,
    parse_upload_payload,
    validate_expiry,
)


def test_parse_public_upload_response() -> None:
    payload = {
        "id": "abc123",
        "name": "file.png",
        "file_url": "https://cdn.example.com/file.png",
        "file_uri": "",
        "is_private": False,
        "mime_type": "image/png",
        "size": 102400,
        "created_date": "2026-08-26T21:33:00Z",
        "view_url": "/file/abc123",
    }
    result = parse_upload_payload(
        payload,
        endpoint="https://dev-cloud.base44.app/functions/uploadFile",
        status=200,
        site_url="https://dev-cloud.base44.app",
    )
    assert result.file_url == "https://cdn.example.com/file.png"
    assert result.file_uri is None
    assert result.is_private is False
    assert result.view_url == "https://dev-cloud.base44.app/file/abc123"


def test_parse_private_upload_accepts_file_uri_without_public_url() -> None:
    payload = {
        "id": "private1",
        "name": "secret.zip",
        "file_url": "",
        "file_uri": "private://secret.zip",
        "is_private": True,
        "mime_type": "application/zip",
        "size": 123,
        "created_date": "2026-08-26T21:33:00Z",
        "view_url": "/file/private1",
    }
    result = parse_upload_payload(
        payload,
        endpoint="https://dev-cloud.base44.app/functions/uploadFile",
        status=200,
        site_url="https://dev-cloud.base44.app",
    )
    assert result.file_url is None
    assert result.file_uri == "private://secret.zip"
    assert result.is_private is True


def test_parse_signed_url_response() -> None:
    result = parse_signed_payload(
        {
            "signed_url": "https://cdn.example.com/signed",
            "expires_in": 3600,
            "expires_at": "2026-08-26T22:33:00Z",
        },
        endpoint="https://dev-cloud.base44.app/functions/createSignedUrl",
        status=200,
    )
    assert result.signed_url == "https://cdn.example.com/signed"
    assert result.expires_in == 3600


def test_parse_shorten_url_response() -> None:
    result = parse_shorten_payload(
        {
            "short_url": "https://dev-cloud.base44.app/s/AbC123",
            "code": "AbC123",
            "expires_at": "2026-08-26T22:33:00Z",
        },
        endpoint="https://dev-cloud.base44.app/functions/shortenUrl",
        status=200,
    )
    assert result.short_url == "https://dev-cloud.base44.app/s/AbC123"
    assert result.code == "AbC123"


def test_shorten_parser_accepts_url_alias() -> None:
    result = parse_shorten_payload(
        {"url": "https://dev-cloud.base44.app/s/test"},
        endpoint="https://dev-cloud.base44.app/functions/shortenUrl",
        status=200,
    )
    assert result.short_url.endswith("/s/test")


def test_signed_request_includes_password_when_protected() -> None:
    payload = build_signed_request_payload(
        " private://secret.zip ",
        expires_in=3600,
        password="secret123",
    )
    assert payload == {
        "file_uri": "private://secret.zip",
        "expires_in": 3600,
        "password": "secret123",
    }


def test_signed_request_omits_password_when_not_protected() -> None:
    payload = build_signed_request_payload("private://secret.zip", expires_in=60)
    assert payload == {
        "file_uri": "private://secret.zip",
        "expires_in": 60,
    }


def test_expiry_range() -> None:
    assert validate_expiry(60) == 60
    assert validate_expiry(MAX_SIGNED_URL_SECONDS) == MAX_SIGNED_URL_SECONDS

    try:
        validate_expiry(59)
    except FileHostError:
        pass
    else:
        raise AssertionError("59 segundos deveria ser rejeitado")


def test_absolute_view_url() -> None:
    assert absolute_view_url(
        "https://dev-cloud.base44.app",
        "/file/abc123",
    ) == "https://dev-cloud.base44.app/file/abc123"
