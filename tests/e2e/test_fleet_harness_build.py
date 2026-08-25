from tests.e2e import fleet_harness


def test_build_binaries_reuses_the_configured_go_cache(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("GOCACHE", "/shared/go-build-cache")
    monkeypatch.setattr(fleet_harness.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    fleet_harness._build_binaries(tmp_path)

    assert len(calls) == 2
    assert all(call[1]["env"]["GOCACHE"] == "/shared/go-build-cache" for call in calls)
    assert all(call[1]["timeout"] == 300 for call in calls)
