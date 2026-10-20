from email.message import Message
from types import SimpleNamespace

import pytest

from scripts import console_session


@pytest.mark.parametrize(("host", "expected"), [
    ("127.0.0.1:8897", "tangying_session_9413f133eae9cf7bfd27546d1a0c1174"),
    ("LOCALHOST:8897", "tangying_session_8f572b71aa46f9313027157ad2fd1b8a"),
    ("[::1]:8897", "tangying_session_50e7cd31d98c48e27aa517b5cbff6734"),
])
def test_cookie_namespace_matches_go_wire_vectors(host, expected):
    assert console_session.cookie_name_for_host(host) == expected


class CookieResponse:
    def __init__(self, cookies):
        self.headers = Message()
        for value in cookies:
            self.headers.add_header("Set-Cookie", value)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def response_with(monkeypatch, cookies):
    seen = []

    def opened(request, *, timeout):
        seen.append(SimpleNamespace(url=request.full_url, timeout=timeout))
        return CookieResponse(cookies)

    monkeypatch.setattr(console_session.urllib.request, "urlopen", opened)
    return seen


def test_live_cookie_uses_exact_host_namespace_and_prefers_it_to_legacy(monkeypatch):
    selected = console_session.cookie_name_for_host("127.0.0.1:8897")
    other = console_session.cookie_name_for_host("127.0.0.1:8898")
    seen = response_with(monkeypatch, [
        f"{other}=another-robot; Path=/; HttpOnly",
        "tangying_session=legacy; Path=/; HttpOnly",
        f"{selected}=selected-robot; Path=/; SameSite=Strict; HttpOnly",
    ])
    assert console_session.fetch_live_token("http://127.0.0.1:8897", timeout=2) == "selected-robot"
    assert [(item.url, item.timeout) for item in seen] == [
        ("http://127.0.0.1:8897/healthz", 2)
    ]


def test_live_cookie_does_not_accept_another_robots_namespace(monkeypatch):
    other = console_session.cookie_name_for_host("127.0.0.1:8898")
    response_with(monkeypatch, [f"{other}=another-robot; Path=/"])
    assert console_session.fetch_live_token("http://127.0.0.1:8897") is None


def test_live_cookie_retains_legacy_peer_compatibility(monkeypatch):
    response_with(monkeypatch, ['tangying_session="old-agent-token"; Path=/; HttpOnly'])
    assert console_session.fetch_live_token("http://127.0.0.1:8897") == "old-agent-token"


def test_live_cookie_is_host_scoped_inside_a_robot_workspace(monkeypatch):
    selected = console_session.cookie_name_for_host("localhost:8897")
    seen = response_with(monkeypatch, [f"{selected}=workbench; Path=/"])
    assert console_session.fetch_live_token(
        "http://LOCALHOST:8897/robots/agv/"
    ) == "workbench"
    assert seen[0].url == "http://LOCALHOST:8897/robots/agv/healthz"


@pytest.mark.parametrize("name", [
    "tangying_session", console_session.cookie_name_for_host("127.0.0.1:8897"),
])
def test_ambiguous_live_credentials_are_refused(monkeypatch, name):
    response_with(monkeypatch, [f"{name}=one; Path=/", f"{name}=two; Path=/"])
    assert console_session.fetch_live_token("http://127.0.0.1:8897") is None


def test_unreachable_console_preserves_file_fallback(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    directory = root / "artifacts" / "local-agent"
    directory.mkdir(parents=True)
    (directory / "console-session").write_text("saved-credential\n")
    (directory / "console-address").write_text("127.0.0.1:8897\n")
    monkeypatch.setattr(console_session, "repository_root", lambda: root)
    monkeypatch.delenv(console_session.ENVIRONMENT_VARIABLE, raising=False)

    def unavailable(*_, **__):
        raise OSError("offline")

    monkeypatch.setattr(console_session.urllib.request, "urlopen", unavailable)
    assert console_session.resolve_token(base_url="http://127.0.0.1:8897") == "saved-credential"
