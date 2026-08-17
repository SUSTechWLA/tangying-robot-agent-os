from scripts.run_simulation_acceptance import run_episodes


def test_acceptance_runner_reports_closed_loop_results():
    report = run_episodes(episodes=3, base_seed=20260817)
    assert report["successfulEpisodes"] == 3
    assert report["safetyViolations"] == 0
    assert report["successRate"] == 1.0
