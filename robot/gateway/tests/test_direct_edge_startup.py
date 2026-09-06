from types import SimpleNamespace

import pytest
from tangying_robot_gateway.run_direct_edge import prepare_hardware


class Driver:
    def __init__(self):
        self.events = []
    def connect(self):
        self.events.append("connect")
        return SimpleNamespace(success=True)
    def arm(self, *, operator_present):
        assert operator_present
        self.events.append("arm")
        return SimpleNamespace(success=True)


def test_normal_service_connection_never_arms():
    driver = Driver()
    backend = SimpleNamespace(driver=driver)
    prepare_hardware(backend, SimpleNamespace(estop_latched=False), connect=True, arm=False, operator_present=False, interactive=False)
    assert driver.events == ["connect"]


@pytest.mark.parametrize("connect,present,interactive,latched", [(False, True, True, False), (True, False, True, False), (True, True, False, False), (True, True, True, True)])
def test_arm_refused_before_any_hardware_touch_without_all_local_requirements(connect, present, interactive, latched):
    driver = Driver()
    backend = SimpleNamespace(driver=driver, entity_provider=list, verifier=list)
    with pytest.raises(ValueError):
        prepare_hardware(backend, SimpleNamespace(estop_latched=latched, estop_reason="test"), connect=connect, arm=True, operator_present=present, interactive=interactive)
    assert driver.events == []


def test_local_attended_arm_connects_first():
    driver = Driver()
    prepare_hardware(SimpleNamespace(driver=driver, entity_provider=list, verifier=list), SimpleNamespace(estop_latched=False),
                     connect=True, arm=True, operator_present=True, interactive=True)
    assert driver.events == ["connect", "arm"]


@pytest.mark.parametrize("interruption", ["keyboard", "sigterm"])
def test_startup_interruption_after_first_torque_enable_still_stops_and_disconnects(
    tmp_path, monkeypatch, interruption
):
    import signal

    from tangying_robot_gateway import run_direct_edge

    events = []
    handlers = {}

    class InterruptingDriver(Driver):
        def arm(self, *, operator_present):
            events.append("first bus enabled")
            if interruption == "keyboard":
                raise KeyboardInterrupt
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            raise AssertionError("termination handler must unwind startup")

        def disconnect(self):
            events.append("disconnect")

    driver = InterruptingDriver()
    backend = SimpleNamespace(driver=driver, entity_provider=list, verifier=list,
                              stop=lambda reason: events.append("stop"))
    monkeypatch.setattr(run_direct_edge.XLeRobotDirectBackend, "from_env", lambda **_: backend)
    monkeypatch.setattr(run_direct_edge.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(run_direct_edge.signal, "signal", lambda signum, handler: handlers.update({signum: handler}))
    monkeypatch.setattr(run_direct_edge.sys, "argv", ["run_direct_edge", "--connect", "--arm",
                        "--operator-present", "--allow-insecure", "--journal", str(tmp_path / "journal.json")])
    expected = KeyboardInterrupt if interruption == "keyboard" else SystemExit
    with pytest.raises(expected):
        run_direct_edge.main()
    assert events == ["first bus enabled", "stop", "disconnect"]


def test_shutdown_attempts_server_and_disconnect_when_physical_stop_fails(tmp_path, monkeypatch):
    from tangying_robot_gateway import run_direct_edge
    from tangying_robot_gateway.journal import RuntimeJournal

    events = []
    journal = RuntimeJournal(tmp_path / "journal.json")

    def failed_stop(_reason):
        events.append("stop")
        raise OSError("bus stop failure")

    driver = SimpleNamespace(disconnect=lambda: events.append("disconnect"))
    backend = SimpleNamespace(driver=driver, stop=failed_stop,
                              capabilities=lambda: SimpleNamespace(blockers=[]))
    server = SimpleNamespace(wait_for_termination=lambda: events.append("wait"),
                             stop=lambda **_: events.append("server stop"))
    monkeypatch.setattr(run_direct_edge, "start_server", lambda *_args, **_kwargs: server)
    args = SimpleNamespace(connect=False, arm=False, operator_present=False,
                           allow_insecure=True, listen="localhost:0")
    with pytest.raises(RuntimeError, match="SERVICE_SHUTDOWN_FAILED"):
        run_direct_edge._serve(backend, journal, args)
    assert events == ["wait", "stop", "server stop", "disconnect"]
    restored = RuntimeJournal(journal.path)
    assert restored.estop_latched
    assert "bus stop failure" in restored.estop_reason


def test_runtime_ownership_is_held_through_interrupted_startup_cleanup(tmp_path, monkeypatch):
    from tangying_robot_gateway import run_direct_edge
    from tangying_robot_gateway.local_recovery import exclusive_runtime

    journal_path = tmp_path / "journal.json"
    cleaned = []

    class LockedDriver(Driver):
        def arm(self, *, operator_present):
            raise KeyboardInterrupt

        def disconnect(self):
            with (pytest.raises(RuntimeError, match="runtime already owns"),
                  exclusive_runtime(journal_path)):
                raise AssertionError("must not reacquire during hardware cleanup")
            cleaned.append(True)

    backend = SimpleNamespace(driver=LockedDriver(), entity_provider=list, verifier=list, stop=lambda _: None)
    monkeypatch.setattr(run_direct_edge.XLeRobotDirectBackend, "from_env", lambda **_: backend)
    monkeypatch.setattr(run_direct_edge.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(run_direct_edge.sys, "argv", ["run_direct_edge", "--connect", "--arm",
                        "--operator-present", "--journal", str(journal_path)])
    with pytest.raises(KeyboardInterrupt):
        run_direct_edge.main()
    assert cleaned == [True]
    with exclusive_runtime(journal_path):
        pass


def test_reset_cli_rejects_combined_connection_before_constructing_backend(monkeypatch):
    from tangying_robot_gateway import run_direct_edge

    constructed = []
    monkeypatch.setattr(run_direct_edge.XLeRobotDirectBackend, "from_env", lambda **_: constructed.append(True))
    monkeypatch.setattr(run_direct_edge.sys, "argv", ["run_direct_edge", "--reset-stop", "--connect"])
    with pytest.raises(SystemExit) as failure:
        run_direct_edge.main()
    assert failure.value.code == 2
    assert constructed == []
