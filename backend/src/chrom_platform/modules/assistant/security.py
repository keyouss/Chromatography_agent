"""Server-side provider URL validation shared by runtime and readiness."""

from __future__ import annotations

import ipaddress
import os
import re
from urllib.parse import urlsplit


_DEFAULT_ALLOWED_HOSTS = frozenset({"api.siliconflow.cn", "api.openai.com"})


def _normalized_endpoint(value: str) -> tuple[str, str, int, str]:
    raw = str(value or "").strip()
    if not raw or any(ord(character) < 0x20 for character in raw) or "\\" in raw or "%" in raw:
        raise ValueError("provider endpoint is invalid")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("provider endpoint must be HTTPS without credentials, query, or fragment")
    try:
        hostname = parsed.hostname
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            host = hostname.encode("idna").decode("ascii").lower().rstrip(".")
            address = None
        else:
            host = address.compressed.lower()
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
                raise ValueError("private provider endpoints are not supported")
        explicit_port = parsed.port
        port = 443 if explicit_port is None else explicit_port
    except (UnicodeError, ValueError) as exc:
        raise ValueError("provider endpoint host or port is invalid") from exc
    if not host or not 1 <= port <= 65535:
        raise ValueError("provider endpoint host or port is invalid")
    path = parsed.path or "/"
    if any(part in {".", ".."} for part in path.split("/")):
        raise ValueError("provider endpoint path is invalid")
    path = path.rstrip("/") or "/"
    return "https", host, port, path


def trusted_provider_url(
    value: str,
    *,
    external_send_policy: str | None = None,
    approved_endpoints: str | None = None,
    allowed_hosts: str | None = None,
) -> str:
    """Validate legacy host references or an exact protected endpoint policy.

    Settings and production Workers must always pass ``external_send_policy``.
    The older host-only path is retained for provider readiness diagnostics.
    """

    raw = str(value or "").strip()
    if external_send_policy is not None:
        if external_send_policy != "approved_endpoint_only":
            raise ValueError("external provider sending is not approved")
        candidates = [item.strip() for item in str(approved_endpoints or "").split(",") if item.strip()]
        if not candidates:
            raise ValueError("no protected provider endpoints are configured")
        target = _normalized_endpoint(raw)
        try:
            approved = {_normalized_endpoint(item) for item in candidates}
        except ValueError as exc:
            raise ValueError("protected provider endpoint configuration is invalid") from exc
        if target not in approved:
            raise ValueError("provider endpoint is not explicitly approved")
        scheme, host, port, path = target
        netloc = f"[{host}]" if ":" in host else host
        if port != 443:
            netloc = f"{netloc}:{port}"
        return f"{scheme}://{netloc}{path}".rstrip("/")

    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("provider URL must be an HTTPS URL without credentials or query parameters")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified):
        raise ValueError("provider URL host is not allowed")
    configured = allowed_hosts if allowed_hosts is not None else os.getenv("LLM_PROVIDER_ALLOWED_HOSTS", "")
    hosts = {item.strip().lower().rstrip(".") for item in configured.split(",") if item.strip()}
    if not hosts:
        hosts = set(_DEFAULT_ALLOWED_HOSTS)
    if host not in hosts:
        raise ValueError("provider URL host is not in the server allowlist")
    return raw.rstrip("/")


__all__ = ["trusted_provider_url"]
