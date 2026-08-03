from __future__ import annotations

import pytest

from mentor_data.normalization import normalize_email, normalized_web_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("xiatao@mail.hust.edu.cn", "xiatao@mail.hust.edu.cn"),
        ("mentor(at)example(dot)edu", "mentor@example.edu"),
        ("mentor [at] example [dot] edu", "mentor@example.edu"),
        ("mentor at example dot edu", "mentor@example.edu"),
    ],
)
def test_email_deobfuscation_does_not_replace_letters_inside_local_part(
    raw: str,
    expected: str,
) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://faculty.example.edu/mentor",
        "http://faculty.example.edu:80/mentor",
        "https://faculty.example.edu/mentor",
        "https://faculty.example.edu:443/mentor",
    ],
)
def test_public_web_url_accepts_http_and_https(url: str) -> None:
    assert normalized_web_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://user:password@faculty.example.edu/mentor",
        "http://faculty.example.edu:8080/mentor",
        "https://faculty.example.edu:8443/mentor",
    ],
)
def test_public_web_url_rejects_unsafe_urls(url: str) -> None:
    assert normalized_web_url(url) is None
