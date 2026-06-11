from discode.bot.session_config_service import (
    _RESERVED_KEYS,
    _validate_api_key,
    _validate_base_url,
    _validate_model,
)


def test_reserved_keys_present():
    assert set(_RESERVED_KEYS.keys()) == {"model", "base_url", "api_key"}


def test_secret_flag_only_for_api_key():
    assert _RESERVED_KEYS["api_key"].secret is True
    assert _RESERVED_KEYS["model"].secret is False
    assert _RESERVED_KEYS["base_url"].secret is False


# model
def test_validate_model_accepts_typical():
    assert _validate_model("claude-3-5-sonnet-20241022") is None


def test_validate_model_rejects_empty():
    assert "non-empty" in (_validate_model("") or "")


def test_validate_model_rejects_too_long():
    assert "128" in (_validate_model("x" * 129) or "")


def test_validate_model_rejects_non_printable():
    assert _validate_model("model\x00name") is not None


# base_url
def test_validate_base_url_accepts_https():
    assert _validate_base_url("https://api.example.com/v1") is None


def test_validate_base_url_accepts_http():
    assert _validate_base_url("http://localhost:11434") is None


def test_validate_base_url_rejects_ftp():
    assert "scheme" in (_validate_base_url("ftp://example.com") or "").lower()


def test_validate_base_url_rejects_no_host():
    assert _validate_base_url("https://") is not None


def test_validate_base_url_rejects_garbage():
    assert _validate_base_url("not a url") is not None


# api_key
def test_validate_api_key_accepts_typical():
    assert _validate_api_key("sk-abc123") is None


def test_validate_api_key_rejects_empty():
    assert _validate_api_key("") is not None


def test_validate_api_key_rejects_too_long():
    assert _validate_api_key("x" * 1025) is not None
