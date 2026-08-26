from bot.api_client import extract_url


def test_extracts_direct_file_url() -> None:
    assert extract_url({"file_url": "https://example.com/a.zip"}) == "https://example.com/a.zip"


def test_extracts_nested_url() -> None:
    assert extract_url({"data": {"result": {"url": "https://cdn.example.com/file.bin"}}}) == "https://cdn.example.com/file.bin"


def test_rejects_non_url_text() -> None:
    assert extract_url("upload concluído") is None
