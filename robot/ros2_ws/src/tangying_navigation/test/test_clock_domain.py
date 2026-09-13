from tangying_navigation.clock_domain import ClockDomain


def test_sim_stamp_preserves_age_and_paused_simulation_becomes_stale():
    clock = ClockDomain()
    assert clock.project(9800, ros_now_ms=10000, unix_now_ms=1000000) == 999800
    assert clock.project(9800, ros_now_ms=10000, unix_now_ms=1006000) == 999800
    assert clock.project(10300, ros_now_ms=10500, unix_now_ms=1007000) == 1006800
    assert clock.project(0, ros_now_ms=10500, unix_now_ms=1007000) == 0
    assert clock.project(99999, ros_now_ms=10500, unix_now_ms=1007000) == 0


def test_resume_does_not_rejuvenate_cached_or_previously_unseen_old_tf():
    clock = ClockDomain()
    assert clock.project(9800, ros_now_ms=10000, unix_now_ms=1000000) == 999800
    assert clock.project(9800, ros_now_ms=10000, unix_now_ms=1006000) == 999800
    assert clock.project(9800, ros_now_ms=10001, unix_now_ms=1006001) == 999800
    assert clock.project(9900, ros_now_ms=10001, unix_now_ms=1006001) == 999900
    assert clock.project(10001, ros_now_ms=10001, unix_now_ms=1006001) == 1006001
