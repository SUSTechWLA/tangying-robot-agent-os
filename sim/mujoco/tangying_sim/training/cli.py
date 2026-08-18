from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .qlearning import CheckpointError, evaluate, load_checkpoint, save_checkpoint, train


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train and evaluate semantic tool policies")
    commands = parser.add_subparsers(dest="command", required=True)

    train_parser = commands.add_parser("train", help="train a seeded Q-learning policy")
    train_parser.add_argument("--episodes", type=int, default=1000)
    train_parser.add_argument("--seed", type=int, default=7)
    train_parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/training/semantic-policy.json"),
    )
    train_parser.add_argument("--max-steps", type=int, default=20)
    train_parser.add_argument("--transient-failure-rate", type=float, default=0.02)

    evaluate_parser = commands.add_parser("evaluate", help="evaluate a policy checkpoint")
    evaluate_parser.add_argument("--checkpoint", type=Path, required=True)
    evaluate_parser.add_argument("--episodes", type=int, default=100)
    evaluate_parser.add_argument("--seed", type=int, default=1007)
    evaluate_parser.add_argument("--min-success-rate", type=float, default=0.90)
    evaluate_parser.add_argument("--transient-failure-rate", type=float, default=0.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except (CheckpointError, ValueError, OSError) as error:
        if isinstance(error, CheckpointError):
            code = "CHECKPOINT_ERROR"
        elif isinstance(error, OSError):
            code = "IO_ERROR"
        else:
            code = "INVALID_ARGUMENT"
        print(
            json.dumps(
                {
                    "command": args.command,
                    "error": {"code": code, "message": str(error)},
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


def _run(args: argparse.Namespace) -> int:
    if args.command == "train":
        policy = train(
            episodes=args.episodes,
            seed=args.seed,
            max_steps=args.max_steps,
            transient_failure_rate=args.transient_failure_rate,
        )
        save_checkpoint(args.output, policy)
        print(
            json.dumps(
                {
                    "command": "train",
                    "checkpoint": str(args.output),
                    "seed": args.seed,
                    **policy.training_summary,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    if not 0.0 <= args.min_success_rate <= 1.0:
        raise ValueError("min-success-rate must be between zero and one")
    policy = load_checkpoint(args.checkpoint)
    report = evaluate(
        policy,
        episodes=args.episodes,
        seed=args.seed,
        transient_failure_rate=args.transient_failure_rate,
    )
    print(
        json.dumps(
            {
                "command": "evaluate",
                "checkpoint": str(args.checkpoint),
                "episodes": report.episodes,
                "successfulEpisodes": report.successful_episodes,
                "successRate": report.success_rate,
                "meanReward": report.mean_reward,
                "byGoalKind": report.by_goal_kind,
                "seed": args.seed,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if report.success_rate >= args.min_success_rate else 1
