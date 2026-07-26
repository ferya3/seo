"""HTTP Basic authentication for the dashboard.

The dashboard drives a crawler and can spend an API key, so on anything
reachable beyond localhost it needs a password. Enforcement lives in the app
rather than only in nginx: a reverse proxy misconfiguration then exposes an
authenticated service instead of an open one.

Set SEO_AGENT_PASSWORD to turn it on. Without it the app refuses to bind to a
non-loopback address (see `require_auth_or_loopback`), so the insecure
combination cannot be reached by accident.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from functools import wraps

from flask import Response, request

REALM = "SEO Agent"


def configured_password() -> str | None:
    password = os.environ.get("SEO_AGENT_PASSWORD", "")
    return password or None


def configured_username() -> str:
    return os.environ.get("SEO_AGENT_USERNAME", "admin")


def is_enabled() -> bool:
    return configured_password() is not None


def _unauthorized() -> Response:
    return Response(
        "برای دسترسی به داشبورد باید وارد شوی.\n",
        401,
        {"WWW-Authenticate": f'Basic realm="{REALM}", charset="UTF-8"'},
        mimetype="text/plain; charset=utf-8",
    )


def check(username: str | None, password: str | None) -> bool:
    expected_password = configured_password()
    if expected_password is None:
        return True
    # compare_digest on both fields so neither is a timing oracle, and so a
    # wrong username costs the same as a wrong password.
    user_ok = secrets.compare_digest((username or "").encode(), configured_username().encode())
    pass_ok = secrets.compare_digest((password or "").encode(), expected_password.encode())
    return user_ok and pass_ok


def protect(view: Callable) -> Callable:
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_enabled():
            return view(*args, **kwargs)
        auth = request.authorization
        if auth is None or not check(auth.username, auth.password):
            return _unauthorized()
        return view(*args, **kwargs)

    return wrapper


def install(app) -> None:
    """Guard every route except the health check."""

    @app.before_request
    def _authenticate():
        if not is_enabled():
            return None
        if request.path == "/healthz":
            return None
        auth = request.authorization
        if auth is None or not check(auth.username, auth.password):
            return _unauthorized()
        return None


def require_auth_or_loopback(host: str) -> None:
    """Refuse to start an unauthenticated server on a public interface.

    Raises SystemExit with an explanation rather than starting something the
    whole internet can drive.
    """
    loopback = host in ("127.0.0.1", "localhost", "::1", "")
    if loopback or is_enabled():
        return
    raise SystemExit(
        "\n  خطا: داشبورد بدون رمز روی آدرس عمومی بالا نمی‌آید.\n\n"
        f"  درخواست بایند روی «{host}» بود ولی SEO_AGENT_PASSWORD تنظیم نشده است.\n"
        "  بدون رمز، هر کسی که به این پورت برسد می‌تواند با سرور تو هر سایتی را بخزد.\n\n"
        "  یکی از این دو را انتخاب کن:\n"
        "    ۱) رمز بگذار:      export SEO_AGENT_PASSWORD='یک-رمز-قوی'\n"
        "    ۲) لوکال نگه دار:  --host 127.0.0.1  و با تونل SSH وصل شو\n"
    )
