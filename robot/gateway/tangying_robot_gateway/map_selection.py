"""Which map should an explore request use: an existing one, or a new survey?

The decision this module makes is the difference between an agent that under-
stands "please explore the environment" and one that needs the operator to spell
out a sequence of gateway calls. It is a *pure* function over already-verified
map evidence, for two reasons:

* the interesting inputs are all slow and authoritative (does a map exist for
  this robot and this calibration, is one active, is the package intact) and
  reading them is the deployment's job, not this module's;
* the answer it gives is a claim an operator will act on - "there is no map here"
  starts a robot driving through a house - so it has to be reproducible from the
  evidence rather than from whatever a model guessed.

The rule, in one sentence: **reuse a verified map for this robot and calibration
if one exists; otherwise survey, and if the caller named a place we cannot match,
ask instead of guessing.**
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: What the caller should do next.
REUSE = "reuse"      #: a verified map already covers this robot and calibration
EXPLORE = "explore"  #: nothing usable exists; a survey has to run
AMBIGUOUS = "ambiguous"  #: the caller named a place and we cannot tell which map it means


@dataclass(frozen=True)
class MapRecord:
    """One verified map package, as the catalog describes it.

    Deliberately narrow. A map is identified by its package id, and the two
    revisions that say whether it still applies to *this* robot: a map built
    against different calibration is not a map of this robot's world, and
    activating it would silently place every obstacle somewhere it is not.
    """

    map_id: str
    name: str = ""
    #: Unix ms the package was created, used only for ordering "newest first".
    created_at_unix_ms: int = 0
    #: True when the package passed its own integrity and revision checks.
    verified: bool = True

    @property
    def label(self) -> str:
        return self.name or self.map_id


@dataclass
class Selection:
    """The routing decision, plus the evidence it was made from."""

    decision: str
    message: str
    map_id: str = ""
    available: list[MapRecord] = field(default_factory=list)
    active_map_id: str = ""
    #: Set when the caller named a place and the name is what made the answer
    #: ambiguous, so the caller can be told what to choose between.
    candidates: list[MapRecord] = field(default_factory=list)

    @property
    def should_explore(self) -> bool:
        return self.decision == EXPLORE

    @property
    def should_reuse(self) -> bool:
        return self.decision == REUSE

    def as_report(self) -> dict:
        """The shape a tool or a console reads.

        ``availableMaps`` is always present, even when the answer is "none": an
        agent that cannot distinguish "no map exists" from "I was not allowed to
        look" will confidently start a survey over a mapped house.
        """
        return {
            "decision": self.decision,
            "message": self.message,
            "mapId": self.map_id,
            "activeMapId": self.active_map_id,
            "availableMaps": [
                {"mapId": record.map_id, "name": record.name,
                 "createdAtUnixMs": record.created_at_unix_ms}
                for record in self.available
            ],
            "matches": [
                {"mapId": record.map_id, "name": record.name}
                for record in self.candidates
            ],
        }


def _mentioned(record: MapRecord, wanted: str) -> bool:
    """Whether a caller naming ``wanted`` could mean this map.

    Matches the map's *name* only, and by substring, case-folded - because an
    operator says "探索客厅" while the package is called "客厅地图", and refusing
    that match would send the robot to survey a room it already has a map of.

    Deliberately not matched against the package id. Ids look like ``scan-3b9d``,
    so a name match against them is only ever accidental, and it makes the rule
    untestable: the id is a random string, not evidence about a place.

    This is substring matching, so it over-matches: Chinese names share characters
    and "车库" is a substring of "厨房地图". That is tolerable rather than correct,
    because a false match can only narrow the answer - and the reply names the map
    it chose, so the operator sees the mistake. A false negative would start a
    twenty-minute survey, which is the worse error by a wide margin. Real
    disambiguation needs a place index, which is a larger change than this rule.
    """
    if not wanted or not record.name:
        return False
    return wanted.casefold() in record.name.casefold()


def select_map(*, available: list[MapRecord], active_map_id: str = "",
               requested: str = "", requested_map_id: str = "") -> Selection:
    """Decide between reusing a verified map and surveying.

    ``available`` must already be filtered to maps that belong to this robot and
    this calibration revision - that filter is a verification step (package
    integrity, manifest schema, revision match), and a decision made on
    unverified packages is a decision to trust a file nobody checked.

    Ordering of the questions matters, and each one exists because of a way this
    can go wrong:

    1. An explicitly named map wins outright. The caller asked for *that* package;
       second-guessing it with a heuristic would be ignoring an instruction.
       A named map that is not in ``available`` is refused rather than surveyed,
       because "the map you asked for is gone" and "there is no map" are
       different answers and only one of them is true.
    2. A named *place* narrows the set. Exactly one match reuses it; several
       matches ask; **no match surveys that place**. The last of those is a
       distinction, not a fallthrough: "explore the garage" with only a kitchen map
       on disk means the garage is unmapped, and quietly reusing the kitchen map
       would answer a request nobody made.
    3. With no place named, any verified map for this robot means reuse - with the
       active one preferred and named in the message, so the reply says *which*
       map it kept.
    4. Otherwise there is nothing to reuse and a survey is the honest answer.
    """
    if requested_map_id:
        match = next((r for r in available if r.map_id == requested_map_id), None)
        if match is None:
            return Selection(
                decision=EXPLORE,
                message=(f"地图 {requested_map_id} 不在本机可用地图中，无法直接使用；"
                         "需要重新扫描建图。"),
                available=list(available), active_map_id=active_map_id)
        return Selection(
            decision=REUSE, map_id=match.map_id,
            message=f"使用已保存的地图「{match.label}」。",
            available=list(available), active_map_id=active_map_id)

    if requested:
        matches = [record for record in available if _mentioned(record, requested)]
        if len(matches) == 1:
            return Selection(
                decision=REUSE, map_id=matches[0].map_id,
                message=f"已有「{matches[0].label}」的地图，直接使用它。",
                available=list(available), active_map_id=active_map_id)
        if len(matches) > 1:
            options = "、".join(f"{record.label}({record.map_id})" for record in matches)
            return Selection(
                decision=AMBIGUOUS,
                message=(f"「{requested}」匹配到多张地图：{options}。"
                         "请说明使用哪一张，或确认重新扫描。"),
                available=list(available), active_map_id=active_map_id,
                candidates=matches)
        # Named a place, nothing covers it. The maps that do exist are of somewhere
        # else, and reusing one of them would be answering a different question.
        return Selection(
            decision=EXPLORE,
            message=(f"本机没有「{requested}」的地图，现在开始自动探索建图。"
                     f"（已有 {len(available)} 张其它地点的地图）" if available else
                     f"本机还没有「{requested}」的地图，现在开始自动探索建图。"),
            available=list(available), active_map_id=active_map_id)

    if available:
        newest = max(available, key=lambda record: record.created_at_unix_ms)
        chosen = next((record for record in available
                       if record.map_id == active_map_id), newest)
        if chosen.map_id == active_map_id:
            message = f"当前已在用「{chosen.label}」的地图，直接使用它。"
        else:
            message = (f"本机已有 {len(available)} 张可用地图，"
                       f"使用最新的「{chosen.label}」。如需重建请明确说明。")
        return Selection(decision=REUSE, map_id=chosen.map_id, message=message,
                         available=list(available), active_map_id=active_map_id)

    return Selection(
        decision=EXPLORE,
        message="本机还没有该环境的地图，现在开始自动探索建图。",
        available=[], active_map_id=active_map_id)
