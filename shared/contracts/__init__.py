"""Machine-readable contracts. `validate` checks a payload against its schema."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

EVENTS_DIR = Path(__file__).parent / "events"


class ContractError(Exception):
    """A payload does not satisfy its declared contract."""


@lru_cache(maxsize=64)
def event_schema(event_type: str) -> dict[str, Any]:
    path = EVENTS_DIR / f"{event_type}.json"
    if not path.exists():
        raise ContractError(f"no schema for event type {event_type!r}")
    return json.loads(path.read_text(encoding="utf-8"))


def known_event_types() -> list[str]:
    return sorted(p.stem for p in EVENTS_DIR.glob("*.json") if p.stem != "envelope")


def validate_event(event_type: str, payload: dict[str, Any]) -> None:
    """Raise ContractError if the payload is invalid.

    Uses jsonschema when installed and falls back to a required-keys check
    otherwise, so a service missing the dependency still catches the common
    mistake instead of silently publishing garbage.
    """
    schema = event_schema(event_type)
    try:
        import jsonschema
    except ImportError:
        missing = set(schema.get("required", [])) - set(payload)
        if missing:
            raise ContractError(
                f"{event_type} payload missing required fields: {sorted(missing)}"
            ) from None
        return

    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        raise ContractError(f"{event_type} payload invalid: {exc.message}") from exc
