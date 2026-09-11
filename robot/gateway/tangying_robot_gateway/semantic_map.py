"""Semantic locations: a configurable "place name -> world pose" registry.

Semantic-first tool calling only works if ``navigate_to("kitchen")`` can be
resolved server-side. Rooms were previously hard-coded in the Go client and in
the MuJoCo home scene, which meant the robot could only understand the one
flat it was built in and the LLM had to be trusted with coordinates.

This module owns that mapping. It is deliberately not a map server: RTAB-Map
and the navigation bridge remain the source of geometric truth. A location is
a *named goal* inside a frame that the navigation stack already publishes.

Design notes:
- Definitions load from JSON so a different flat is configuration, not code.
- The default layout matches the commissioned four-room reference scene, so the
  shipped defaults stay runnable.
- Aliases exist because users say "厨房", "kitchen" and "the kitchen"
  interchangeably; matching stays exact after normalisation, so an unknown name
  fails loudly instead of resolving to the nearest guess.
"""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_LAYOUT_PATH = Path(__file__).with_name("assets") / "home_locations.json"


class LocationError(ValueError):
    """Raised for invalid layout definitions, never for an unknown lookup."""


def normalize_location_name(value: str) -> str:
    """Casefold, strip width/space noise and drop a leading article.

    Only *presentation* differences are normalised. Two genuinely different
    rooms never collide, because nothing here does fuzzy matching.
    """

    if not isinstance(value, str):
        raise LocationError("location name must be a string")
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    # Collapse internal whitespace so "the  kitchen" == "the kitchen".
    text = " ".join(text.split())
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article):]
            break
    return text


@dataclass(frozen=True)
class SemanticLocation:
    """A named goal: a pose, the frame it lives in and how it may be addressed."""

    name: str
    pose: tuple[float, float, float]
    room: str
    frame_id: str = "map"
    aliases: tuple[str, ...] = ()
    reachable: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise LocationError("location requires a name")
        if len(self.pose) != 3 or not all(_finite(value) for value in self.pose):
            raise LocationError(f"location {self.name} requires a finite (x, y, theta) pose")
        if not self.frame_id.strip():
            raise LocationError(f"location {self.name} requires a frame id")

    @property
    def x(self) -> float:
        return self.pose[0]

    @property
    def y(self) -> float:
        return self.pose[1]

    @property
    def theta(self) -> float:
        return self.pose[2]

    def to_pose7(self) -> list[float]:
        """The 7-element ``[x, y, z, qw, qx, qy, qz]`` pose the runtime accepts."""

        half = self.theta / 2.0
        return [self.x, self.y, 0.0, math.cos(half), 0.0, 0.0, math.sin(half)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "room": self.room, "frame_id": self.frame_id,
            "x": self.x, "y": self.y, "theta": self.theta,
            "pose": self.to_pose7(), "reachable": self.reachable,
            "description": self.description,
        }


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


class SemanticMap:
    """A lookup table of named goals, with explicit unknown-name handling."""

    def __init__(self, locations: list[SemanticLocation], *, layout_id: str = "") -> None:
        self._locations: dict[str, SemanticLocation] = {}
        self._index: dict[str, str] = {}
        self.layout_id = layout_id
        for location in locations:
            if location.name in self._locations:
                raise LocationError(f"duplicate location {location.name!r}")
            self._locations[location.name] = location
            for label in (location.name, *location.aliases):
                key = normalize_location_name(label)
                existing = self._index.get(key)
                if existing is not None and existing != location.name:
                    raise LocationError(
                        f"alias {label!r} is ambiguous between {existing!r} and {location.name!r}"
                    )
                self._index[key] = location.name

    # -- construction ----------------------------------------------------

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SemanticMap:
        if not isinstance(payload, dict):
            raise LocationError("layout must be a JSON object")
        raw = payload.get("locations")
        if not isinstance(raw, list) or not raw:
            raise LocationError("layout requires a non-empty 'locations' list")
        locations = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise LocationError("each location must be a JSON object")
            pose = entry.get("pose")
            if not isinstance(pose, list) or len(pose) != 3:
                raise LocationError(f"location {entry.get('name')!r} requires a 3-element pose")
            aliases = entry.get("aliases") or []
            if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
                raise LocationError(f"location {entry.get('name')!r} aliases must be strings")
            locations.append(SemanticLocation(
                name=str(entry.get("name", "")).strip(),
                pose=(float(pose[0]), float(pose[1]), float(pose[2])),
                room=str(entry.get("room") or entry.get("name") or "").strip(),
                frame_id=str(entry.get("frame_id") or "map").strip(),
                aliases=tuple(aliases),
                reachable=bool(entry.get("reachable", True)),
                description=str(entry.get("description") or ""),
            ))
        return cls(locations, layout_id=str(payload.get("layout_id") or ""))

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> SemanticMap:
        target = Path(path) if path is not None else DEFAULT_LAYOUT_PATH
        try:
            payload = json.loads(target.read_text())
        except FileNotFoundError as exc:
            raise LocationError(f"semantic layout not found: {target}") from exc
        except json.JSONDecodeError as exc:
            raise LocationError(f"semantic layout {target} is not valid JSON: {exc}") from exc
        return cls.from_dict(payload)

    # -- lookup ----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._locations)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._locations))

    def all(self) -> tuple[SemanticLocation, ...]:
        return tuple(self._locations[name] for name in self.names())

    def rooms(self) -> tuple[str, ...]:
        return tuple(sorted({location.room for location in self._locations.values()}))

    def resolve(self, location_name: str) -> SemanticLocation | None:
        """Resolve a user-supplied name, or ``None`` when it is not defined."""

        return self._locations.get(self._index.get(normalize_location_name(location_name), ""))

    def describe(self) -> list[dict[str, Any]]:
        return [location.to_dict() for location in self.all()]
