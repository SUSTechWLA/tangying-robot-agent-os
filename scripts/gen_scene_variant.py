#!/usr/bin/env python3
"""Generate world-offset MuJoCo scene variants for the multi-robot fleet.

MJCF has no parameterization, so a second robot instance needs a shifted
copy of the scene: every body/camera/light position in the tabletop scene
(and the included XLeRobot model) is translated by (dx, dy). Because the
offset is baked into the XML, all runtime telemetry (robot base_pose and
entity poses) is already expressed in the shared world frame and the fleet
fusion service can merge the two robots' reports directly.

Usage:
  python scripts/gen_scene_variant.py --src assets/xlerobot_tabletop.xml \\
      --dst assets/xlerobot_tabletop_r2.xml --dx 2.0 --dy 0 \\
      --include xlerobot/xlerobot.xml=xlerobot/xlerobot_r2.xml
  python scripts/gen_scene_variant.py --src assets/xlerobot/xlerobot.xml \\
      --dst assets/xlerobot/xlerobot_r2.xml --dx 2.0 --dy 0
"""

import argparse
import sys
import xml.etree.ElementTree as ET

SHIFTED_TAGS = {"body", "camera", "light"}


def shift(source: str, target: str, dx: float, dy: float, include_map: dict[str, str]) -> None:
    tree = ET.parse(source)
    root = tree.getroot()
    parents = build_parent_map(root)
    # Only worldbody DIRECT children carry world-frame positions; nested
    # bodies/cameras are local to their parent and must not be shifted or
    # the robot kinematics break (e.g. the arms of the included XLeRobot).
    for element in root.iter():
        if element.tag == "include":
            original = element.get("file")
            if original in include_map:
                element.set("file", include_map[original])
            continue
        if element.tag not in SHIFTED_TAGS:
            continue
        parent = parents.get(element)
        if parent is None or parent.tag != "worldbody":
            continue
        pos = element.get("pos")
        if pos is None:
            continue
        parts = pos.split()
        if len(parts) < 2:
            continue
        try:
            x, y = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        parts[0] = format(x + dx, ".12g")
        parts[1] = format(y + dy, ".12g")
        element.set("pos", " ".join(parts))
    tree.write(target, encoding="utf-8", xml_declaration=True)
    print(f"wrote {target} (dx={dx}, dy={dy})")


def build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    parents: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parents[child] = parent
    return parents


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="source XML")
    parser.add_argument("--dst", required=True, help="destination XML")
    parser.add_argument("--dx", type=float, default=0.0)
    parser.add_argument("--dy", type=float, default=0.0)
    parser.add_argument("--include", action="append", default=[],
                        help="file=newfile mapping for <include> elements")
    args = parser.parse_args()
    include_map: dict[str, str] = {}
    for mapping in args.include:
        if "=" not in mapping:
            print(f"error: --include must be file=newfile, got {mapping}", file=sys.stderr)
            sys.exit(2)
        original, replacement = mapping.split("=", 1)
        include_map[original] = replacement
    shift(args.src, args.dst, args.dx, args.dy, include_map)


if __name__ == "__main__":
    main()
