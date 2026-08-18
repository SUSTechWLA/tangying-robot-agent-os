from scripts.run_simulation_acceptance import run_episodes, run_object_matrix, run_two_goal_sequence


def test_acceptance_runner_reports_closed_loop_results():
    report = run_episodes(episodes=3, base_seed=20260817)
    assert report["successfulEpisodes"] == 3
    assert report["safetyViolations"] == 0
    assert report["successRate"] == 1.0


def test_simulation_covers_every_advertised_object_and_destination():
    report = run_object_matrix(base_seed=20260817)
    assert report["goals"] == 18
    assert report["successfulGoals"] == 18
    assert report["successRate"] == 1.0


def test_acceptance_runner_executes_the_user_two_goal_sequence_in_order():
    report = run_two_goal_sequence(seed=20260819)

    assert report["success"] is True
    assert report["safetyViolations"] == 0
    assert report["placements"] == {
        "red-cup": "right-bin",
        "blue-bottle": "front-tray",
    }
    assert [goal["objectId"] for goal in report["goals"]] == ["red-cup", "blue-bottle"]
