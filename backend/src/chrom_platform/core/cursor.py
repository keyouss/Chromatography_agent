"""Opaque, scope-bound continuation cursors for keyset pagination.

The first backend baseline has no stable application secret port. Cursors are
therefore encoded continuations rather than signed claims: callers must treat
them as opaque, and the server validates their scope and shape before use.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Any


class CursorError(ValueError):
    """Raised when a cursor is malformed or bound to another scope."""


_SIGNED_TOKEN = re.compile(r"^([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]{43})$")
_ROW_ID_MAX_CHARS = 96
_ROW_ID_MAX_UTF8_BYTES = 384
_SOURCE_ROW_NUMBER_MAX = 2_147_483_647  # Signed MySQL INTEGER / SQLAlchemy Integer.


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CursorError("invalid cursor state") from exc


def _decode_json_without_duplicate_keys(raw: bytes) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CursorError("invalid cursor")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=object_pairs)
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise CursorError("invalid cursor") from exc
    if _canonical_json(value) != raw:
        raise CursorError("invalid cursor")
    return value


def _validate_position(after: Any) -> tuple[int, str]:
    if not isinstance(after, list) or len(after) != 2:
        raise CursorError("invalid cursor position")
    row_number, row_id = after
    if (not isinstance(row_number, int) or isinstance(row_number, bool)
            or row_number < 1 or row_number > _SOURCE_ROW_NUMBER_MAX):
        raise CursorError("invalid cursor position")
    if (not isinstance(row_id, str) or not row_id or len(row_id) > _ROW_ID_MAX_CHARS
            or "\x00" in row_id):
        raise CursorError("invalid cursor position")
    try:
        if len(row_id.encode("utf-8", errors="strict")) > _ROW_ID_MAX_UTF8_BYTES:
            raise CursorError("invalid cursor position")
    except UnicodeError as exc:
        raise CursorError("invalid cursor position") from exc
    return row_number, row_id


@dataclass(frozen=True)
class CursorCodec:
    @staticmethod
    def scope(actor_id: str, project_ids: list[str]) -> str:
        payload = {"actor_id": actor_id, "project_ids": project_ids, "sort": "id.asc"}
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def encode(self, *, after_id: str, scope: str) -> str:
        if not after_id or not scope:
            raise CursorError("invalid cursor state")
        raw = json.dumps(
            {"v": 1, "after_id": after_id, "scope": scope},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def decode(self, token: str, *, scope: str) -> str:
        try:
            if not token or "." in token:
                raise CursorError("invalid cursor")
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            payload = json.loads(raw.decode("utf-8"))
            if set(payload) != {"v", "after_id", "scope"} or payload["v"] != 1:
                raise CursorError("unsupported cursor")
            after_id = payload["after_id"]
            if payload["scope"] != scope or not isinstance(after_id, str) or not after_id:
                raise CursorError("cursor scope mismatch")
            return after_id
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise CursorError("invalid cursor") from exc


@dataclass(frozen=True)
class SignedCursorCodec:
    """Explicit-key signed row-position cursors for protected pagination scopes."""

    key: bytes = field(repr=False)
    max_token_length: int = 1024

    def __post_init__(self) -> None:
        if not isinstance(self.key, bytes) or len(self.key) != 32:
            raise ValueError("cursor signing key must contain exactly 32 bytes")
        if self.max_token_length < 256:
            raise ValueError("cursor token limit is too small")

    def encode(self, *, after: tuple[int, str] | list[Any], scope: str) -> str:
        if not isinstance(scope, str) or not re.fullmatch(r"[0-9a-f]{64}", scope):
            raise CursorError("invalid cursor state")
        if not isinstance(after, (tuple, list)):
            raise CursorError("invalid cursor state")
        row_number, row_id = _validate_position(list(after))
        raw = _canonical_json({"after": [row_number, row_id], "scope": scope, "v": 1})
        body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        signature = base64.urlsafe_b64encode(hmac.new(self.key, body.encode("ascii"), hashlib.sha256).digest()).rstrip(b"=").decode("ascii")
        token = f"{body}.{signature}"
        if len(token) > self.max_token_length:
            raise CursorError("cursor exceeds maximum length")
        return token

    def decode(self, token: str, *, scope: str) -> tuple[int, str]:
        if not isinstance(token, str) or not token or len(token) > self.max_token_length:
            raise CursorError("invalid cursor")
        match = _SIGNED_TOKEN.fullmatch(token)
        if match is None:
            raise CursorError("invalid cursor")
        body, encoded_signature = match.groups()
        try:
            raw = base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
            signature = base64.b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise CursorError("invalid cursor") from exc
        if (base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != body
                or base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii") != encoded_signature):
            raise CursorError("invalid cursor")
        expected = hmac.new(self.key, body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise CursorError("invalid cursor")
        payload = _decode_json_without_duplicate_keys(raw)
        if not isinstance(payload, dict) or set(payload) != {"after", "scope", "v"}:
            raise CursorError("invalid cursor")
        if (not isinstance(payload["v"], int) or isinstance(payload["v"], bool) or payload["v"] != 1):
            raise CursorError("unsupported cursor")
        if not isinstance(scope, str) or not re.fullmatch(r"[0-9a-f]{64}", scope) or payload["scope"] != scope:
            raise CursorError("cursor scope mismatch")
        return _validate_position(payload["after"])


__all__ = ["CursorCodec", "CursorError", "SignedCursorCodec"]
