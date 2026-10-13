#!/usr/bin/env python3
"""Build a bounded 3-D navigation package from externally registered measurements.

This offline importer does not acquire ROS sensors or estimate SLAM. Input is the
slam.registered-measurements.v1 document emitted by provider.measurements().
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "robot" / "gateway"))

from tangying_robot_gateway.spatial_mapping import (
    RegisteredSpatialSlamProvider,
    read_spatial_json,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="registered measurement history JSON")
    parser.add_argument("--output", required=True, type=Path, help="new immutable package directory")
    parser.add_argument("--reference-time-unix-ms", type=int,
                        help="explicit historical replay time; default is current Unix milliseconds")
    parser.add_argument("--terrain", type=Path, help="optional separately measured navigation.terrain.v1 JSON")
    args = parser.parse_args(argv)
    try:
        history = read_spatial_json(args.input)
        provider = RegisteredSpatialSlamProvider.restore(history)
        reference = int(time.time() * 1000) if args.reference_time_unix_ms is None else args.reference_time_unix_ms
        # restore validates historical order; importing into a freshly published
        # package additionally checks the caller's explicitly selected time basis.
        if type(reference) is not int or reference <= 0:
            raise ValueError("reference time must be positive Unix milliseconds")
        for frame in history["frames"]:
            if not 0 <= reference - frame["observedAtUnixMs"] <= provider.max_measurement_age_ms:
                raise ValueError("registered measurement is stale or future dated at selected reference time")
        terrain = None
        if args.terrain is not None:
            terrain = read_spatial_json(args.terrain, maximum=16 * 1024 * 1024)
        manifest = provider.publish(args.output, terrain=terrain)
        print(json.dumps({"package": str(args.output.absolute()), "revision": manifest["revision"],
                          "mapRevision": manifest["mapRevision"], "frameCount": provider.frame_count,
                          "referenceTimeUnixMs": reference, "evidenceKind": "externally_registered_measurements",
                          "hardwareValidated": False}, ensure_ascii=False))
        return 0
    except (OSError, TypeError, KeyError, ValueError) as error:
        print(f"spatial map build rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
