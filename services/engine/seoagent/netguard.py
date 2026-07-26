"""Guard against pointing the crawler at internal infrastructure.

On a public VPS an unguarded crawler is an SSRF proxy: anyone who can reach the
dashboard can make the server fetch `http://169.254.169.254/` (cloud instance
metadata — IAM credentials on AWS/GCP/Azure), or sweep the private network the
VPS sits on. Neither is something the user asked for by typing a URL.

Blocking is on by default. Set SEO_AGENT_ALLOW_PRIVATE=1 to audit sites on
localhost or a LAN, which is the normal case when running this on your laptop.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from functools import lru_cache
from urllib.parse import urlparse

# Cloud metadata endpoints. These are link-local (169.254.0.0/16) and so are
# already covered by the range check, but they are named here because they are
# the reason this module exists.
METADATA_HOSTS = {
    "169.254.169.254",       # AWS, Azure, DigitalOcean, OpenStack
    "metadata.google.internal",
    "100.100.100.200",       # Alibaba Cloud
}

ALLOWED_SCHEMES = ("http", "https")


class TargetNotAllowed(Exception):
    """The requested target resolves somewhere we refuse to fetch."""


def allow_private() -> bool:
    return os.environ.get("SEO_AGENT_ALLOW_PRIVATE", "").strip().lower() in ("1", "true", "yes", "on")


def _is_blocked_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


@lru_cache(maxsize=2048)
def _resolve(host: str) -> tuple[str, ...]:
    """All addresses a hostname resolves to. Cached — a crawl hits the same
    host hundreds of times and we don't want a DNS lookup per request."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise TargetNotAllowed(f"نام دامنه «{host}» قابل تبدیل به آی‌پی نیست: {exc.strerror or exc}") from exc
    return tuple({info[4][0] for info in infos})


def check_url(url: str) -> None:
    """Raise :class:`TargetNotAllowed` if this URL must not be fetched."""
    parsed = urlparse(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise TargetNotAllowed(f"فقط http و https پشتیبانی می‌شوند (دریافت شد: «{parsed.scheme or '—'}»)")

    host = parsed.hostname
    if not host:
        raise TargetNotAllowed("آدرس میزبان ندارد.")

    # Metadata endpoints are blocked unconditionally — SEO_AGENT_ALLOW_PRIVATE
    # exists so you can audit a staging site on your LAN, which is no reason to
    # hand out the server's cloud IAM credentials.
    if host.lower() in METADATA_HOSTS or host.lower().endswith(".internal"):
        raise TargetNotAllowed(
            f"آدرس «{host}» به سرویس metadata سرور اشاره می‌کند و در هیچ حالتی مجاز نیست."
        )

    if allow_private():
        return

    # A literal IP is checked directly; a name is checked against every address
    # it resolves to, so a DNS record pointing at 127.0.0.1 is caught too.
    for address in _resolve(host):
        if _is_blocked_ip(address):
            raise TargetNotAllowed(
                f"آدرس «{host}» به {address} در شبکه‌ی داخلی اشاره می‌کند و مجاز نیست. "
                "اگر واقعاً می‌خواهی یک سایت لوکال را بررسی کنی، "
                "متغیر SEO_AGENT_ALLOW_PRIVATE=1 را تنظیم کن."
            )


def is_allowed(url: str) -> bool:
    try:
        check_url(url)
    except TargetNotAllowed:
        return False
    return True


def reset_cache() -> None:
    """Drop the DNS cache — used by tests and after a long-running process
    has been up long enough for records to have changed."""
    clear = getattr(_resolve, "cache_clear", None)
    if clear is not None:  # absent when a test has substituted the resolver
        clear()
