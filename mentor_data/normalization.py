from __future__ import annotations

import re
import unicodedata
from html import unescape
from urllib.parse import urlsplit

EMAIL_AT_PATTERN = re.compile(
    r"(?i)(?:\[\s*at\s*\]|\(\s*at\s*\)|（\s*at\s*）|(?<=\s)at(?=\s)|艾特)"
)
EMAIL_DOT_PATTERN = re.compile(
    r"(?i)(?:\[\s*dot\s*\]|\(\s*dot\s*\)|（\s*dot\s*）|(?<=\s)dot(?=\s))"
)
EMAIL_CHINESE_DOT_PATTERN = re.compile(r"(?<=[A-Za-z0-9])\s*[点點]\s*(?=[A-Za-z0-9])")
EMAIL_INVISIBLE_PATTERN = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
EMAIL_LOCAL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
EMAIL_DOMAIN_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
WHITESPACE_PATTERN = re.compile(r"\s+")

FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "＠": "@",
        "﹫": "@",
        "．": ".",
        "。": ".",
        "｡": ".",
    }
)

GENERIC_EMAIL_LOCAL_PARTS = {
    "admin",
    "admission",
    "admissions",
    "contact",
    "faculty",
    "graduate",
    "hr",
    "info",
    "office",
    "recruit",
    "secretary",
    "support",
}


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return WHITESPACE_PATTERN.sub(" ", normalized)


def normalize_name_key(value: str | None) -> str:
    return normalize_text(value).casefold()


def normalize_organization_key(value: str | None) -> str:
    normalized = normalize_text(value).casefold()
    return re.sub(r"[\s·•・,，.。()（）\[\]【】]", "", normalized)


def normalize_email(value: str | None) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", unescape(str(value))).strip().lower()
    normalized = normalized.translate(FULLWIDTH_TRANSLATION)
    normalized = EMAIL_INVISIBLE_PATTERN.sub("", normalized)
    normalized = EMAIL_CHINESE_DOT_PATTERN.sub(".", normalized)
    normalized = EMAIL_AT_PATTERN.sub("@", normalized)
    normalized = EMAIL_DOT_PATTERN.sub(".", normalized)
    normalized = re.sub(r"\s*@\s*", "@", normalized)
    normalized = re.sub(r"\s*\.\s*", ".", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    if normalized.count("@") == 1:
        local_part, domain = normalized.split("@", 1)
        domain = re.sub(r"\.{2,}", ".", domain).strip(".")
        if domain:
            normalized = f"{local_part}@{domain}"
    return normalized


def is_valid_email(value: str) -> bool:
    if not value or len(value) > 255 or value.count("@") != 1:
        return False
    local_part, domain = value.split("@", 1)
    if not local_part or len(local_part) > 64 or not EMAIL_LOCAL_PATTERN.fullmatch(local_part):
        return False
    labels = domain.split(".")
    if len(labels) < 2 or not all(EMAIL_DOMAIN_LABEL_PATTERN.fullmatch(item) for item in labels):
        return False
    return labels[-1].isalpha() and len(labels[-1]) >= 2


def is_generic_email(value: str) -> bool:
    normalized = normalize_email(value)
    if "@" not in normalized:
        return False
    local_part = normalized.split("@", 1)[0]
    collapsed = re.sub(r"[._+-].*$", "", local_part)
    return local_part in GENERIC_EMAIL_LOCAL_PARTS or collapsed in GENERIC_EMAIL_LOCAL_PARTS


def normalized_https_url(value: str | None) -> str | None:
    normalized = normalize_text(value)
    if not normalized or len(normalized) > 500:
        return None
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if port not in (None, 443):
        return None
    return parsed.geturl()


def normalized_web_url(value: str | None) -> str | None:
    normalized = normalize_text(value)
    if not normalized or len(normalized) > 500:
        return None
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    expected_port = 80 if parsed.scheme == "http" else 443
    if port not in (None, expected_port):
        return None
    return parsed.geturl()


def hostname_for_url(value: str) -> str:
    parsed = urlsplit(value)
    return (parsed.hostname or "").lower().rstrip(".")


def host_matches_domain(hostname: str, domain: str) -> bool:
    host = hostname.lower().rstrip(".")
    allowed = domain.lower().rstrip(".")
    return host == allowed or host.endswith(f".{allowed}")
