from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def json_bytes(value: Any, *, pretty: bool = True) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return text.encode("utf-8")


def write_json_atomic(path: Path, value: Any, *, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json_bytes(value, pretty=pretty)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_yaml_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump(
        value,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def iter_json_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(candidate for candidate in path.rglob("*.json") if candidate.is_file())
