"""Command-line argument routing."""

from __future__ import annotations

import pytest

from seoagent.__main__ import main


def run(argv, monkeypatch) -> dict:
    """Run main() with the serve handler stubbed, and report what it parsed."""
    seen: dict = {}

    def fake_serve(args):
        seen.update(host=args.host, port=args.port, open=args.open)
        return 0

    import seoagent.__main__ as cli

    monkeypatch.setattr(cli, "_serve", fake_serve)
    assert main(argv) == 0
    return seen


def test_no_arguments_starts_the_server(monkeypatch):
    assert run([], monkeypatch)["port"] == 5000


def test_bare_flags_are_routed_to_serve(monkeypatch):
    """`seoagent --host 0.0.0.0` must work, not die on 'unrecognized arguments'."""
    parsed = run(["--host", "0.0.0.0", "--port", "8080", "--no-open"], monkeypatch)
    assert parsed == {"host": "0.0.0.0", "port": 8080, "open": False}


def test_explicit_serve_still_works(monkeypatch):
    assert run(["serve", "--port", "9000"], monkeypatch)["port"] == 9000


def test_unknown_flag_is_still_an_error(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        run(["--nonsense"], monkeypatch)
    assert exc.value.code == 2


def test_unknown_subcommand_is_not_swallowed(monkeypatch):
    """A typo'd subcommand should fail, not be treated as a serve flag."""
    with pytest.raises(SystemExit):
        run(["audlt", "example.com"], monkeypatch)
