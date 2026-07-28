"""Serve the fixture sites over real HTTP so the crawler is exercised end to end."""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import socket
import socketserver
import threading
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "engine" / "tests" / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def allow_local_fixture_sites():
    """The fixture sites live on 127.0.0.1, which the SSRF guard blocks by
    default. Opt in for the whole suite; `test_security.py` unsets it again to
    exercise the guard itself."""
    os.environ["SEO_AGENT_ALLOW_PRIVATE"] = "1"
    yield
    os.environ.pop("SEO_AGENT_ALLOW_PRIVATE", None)




class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Adds extensionless routing (/about → about.html) and keeps the log quiet."""

    def log_message(self, *args, **kwargs) -> None:
        pass

    def translate_path(self, path: str) -> str:
        resolved = super().translate_path(path)
        candidate = Path(resolved)
        if not candidate.exists() and not candidate.suffix:
            with_html = candidate.with_suffix(".html")
            if with_html.exists():
                return str(with_html)
        return resolved


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(directory: Path) -> tuple[str, socketserver.TCPServer]:
    port = _free_port()
    handler = functools.partial(_QuietHandler, directory=str(directory))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", httpd


def _prepare(name: str, tmp_path: Path) -> Path:
    """Copy a fixture site and substitute the PORT placeholder with a real port."""
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return target


@pytest.fixture(scope="session")
def bad_site(tmp_path_factory) -> str:
    directory = _prepare("badsite", tmp_path_factory.mktemp("bad"))
    base, httpd = _serve(directory)
    yield base
    httpd.shutdown()


@pytest.fixture(scope="session")
def good_site(tmp_path_factory) -> str:
    directory = _prepare("goodsite", tmp_path_factory.mktemp("good"))
    port = _free_port()
    # The fixture references absolute URLs (canonical, sitemap), so bake the
    # port in before serving.
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix in (".html", ".xml", ".txt"):
            path.write_text(path.read_text(encoding="utf-8").replace("PORT", str(port)), encoding="utf-8")

    handler = functools.partial(_QuietHandler, directory=str(directory))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
