"""Physical properties of an object, as observed data rather than code constants.

A grasp is not one behaviour: a ceramic mug, a paper cup and a glass vase share a
category prefix in nobody's catalogue and need different treatment. The system
previously encoded what it knew about objects as literals in the runtime - the
placement height was ``{"cup": 0.06, "bottle": 0.08}[category]`` and the grasp
tolerance was a class constant - so a new object meant editing the driver, and an
unknown one raised ``KeyError`` instead of saying "I have no profile for this".

This module is the vocabulary and the budget derivation, deliberately separate
from any driver:

* **Attributes are optional.** An object that declares nothing keeps exactly the
  behaviour the commissioned workcell had, so introducing the vocabulary cannot
  change a single existing grasp.
* **Unknown is a value, not an error.** ``material`` may be absent or ``unknown``;
  the caller gets the conservative default budget and the record says the
  material was not identified.
* **Invalid is an error.** A declared ``material`` outside the vocabulary, or a
  non-numeric mass, is a data defect: it is refused loudly rather than silently
  falling back to a default that might be wrong for a fragile object.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: What a sighting may declare about an object's physical behaviour. ``unknown``
#: is spelled out because "we looked and could not tell" is different from "the
#: detector never ran".
MATERIALS = ("rigid", "soft", "fragile", "deformable", "granular", "unknown")

#: Numeric attributes and their accepted ranges, in SI units.
_NUMERIC_ATTRIBUTES = {
    "mass_g": (0.0, 1_000_000.0),
    "max_grip_force_n": (0.05, 500.0),
}

#: Placement height above a destination surface, per category. This is the
#: commissioned workcell's geometry, kept as data so a driver without a better
#: source can still place what it was commissioned for. A category outside this
#: table is refused with a reason instead of raising KeyError.
PLACEMENT_HALF_HEIGHT_M = {"cup": 0.06, "bottle": 0.08}


class PhysicalAttributeError(ValueError):
    """A declared attribute is outside the vocabulary or malformed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PhysicalProfile:
    """What is known about an object's physical behaviour."""

    material: str = "unknown"
    mass_g: float | None = None
    max_grip_force_n: float | None = None
    declared: bool = False

    @property
    def identified(self) -> bool:
        return self.declared and self.material != "unknown"


@dataclass(frozen=True)
class GraspBudget:
    """The bounded budget one grasp is allowed to spend.

    These are the numbers a driver acts on, published with the step so "why did
    it grip so gently" is answerable from the record rather than from the code.
    """

    tolerance_m: float
    max_force_n: float
    close_rate_scale: float
    #: True when the budget came from declared properties rather than defaults.
    from_declaration: bool
    material: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {"toleranceM": round(self.tolerance_m, 4),
                "maxForceN": round(self.max_force_n, 3),
                "closeRateScale": round(self.close_rate_scale, 3),
                "fromDeclaration": self.from_declaration,
                "material": self.material}


#: The commissioned workcell's grasp, used whenever nothing is declared.
DEFAULT_TOLERANCE_M = 0.055
DEFAULT_MAX_FORCE_N = 40.0

#: Material-specific budgets. A fragile object is grasped with a tighter
#: alignment requirement and a capped force; a soft one is squeezed slowly so
#: the contact can be released before it deforms. Values are conservative
#: defaults for a two-finger gripper, not calibrated limits for a specific
#: hand - a deployment overrides them through declared attributes.
_MATERIAL_BUDGETS = {
    "rigid": (DEFAULT_TOLERANCE_M, DEFAULT_MAX_FORCE_N, 1.0),
    "soft": (0.035, 12.0, 0.5),
    "fragile": (0.025, 6.0, 0.35),
    "deformable": (0.030, 10.0, 0.4),
    "granular": (0.040, 15.0, 0.6),
    "unknown": (DEFAULT_TOLERANCE_M, DEFAULT_MAX_FORCE_N, 1.0),
}


def physical_profile(attributes: Any) -> PhysicalProfile:
    """Read and validate an entity's declared physical properties.

    Accepts the entity attribute mapping as it travels today (string to string),
    which is why numeric values are parsed here instead of being typed in the
    transport contract.
    """
    if attributes is None:
        return PhysicalProfile()
    if not isinstance(attributes, dict):
        raise PhysicalAttributeError("PHYSICAL_ATTRIBUTES_INVALID",
                                     "object attributes must be a mapping")
    material = str(attributes.get("material", "") or "").strip().lower() or "unknown"
    if material not in MATERIALS:
        raise PhysicalAttributeError(
            "PHYSICAL_ATTRIBUTE_UNKNOWN",
            f"material {material!r} is not one of {', '.join(MATERIALS)}")
    values: dict[str, float] = {}
    for key, (low, high) in _NUMERIC_ATTRIBUTES.items():
        raw = attributes.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            value = float(str(raw))
        except (TypeError, ValueError) as error:
            raise PhysicalAttributeError(
                "PHYSICAL_ATTRIBUTE_INVALID", f"{key} must be a number, got {raw!r}") from error
        if not math.isfinite(value) or not low <= value <= high:
            raise PhysicalAttributeError(
                "PHYSICAL_ATTRIBUTE_INVALID",
                f"{key}={value} is outside the accepted range {low}..{high}")
        values[key] = value
    declared = material != "unknown" or bool(values)
    return PhysicalProfile(material=material, mass_g=values.get("mass_g"),
                           max_grip_force_n=values.get("max_grip_force_n"),
                           declared=declared)


def grasp_budget(profile: PhysicalProfile, *, force_n: float | None = None) -> GraspBudget:
    """The budget for one grasp, honouring a declared force cap above everything.

    Precedence is deliberate: an object that states its own limit wins over the
    material default, because the limit is a fact about that object and the
    material is a category.
    """
    tolerance, default_force, rate = _MATERIAL_BUDGETS.get(
        profile.material, _MATERIAL_BUDGETS["unknown"])
    ceiling = profile.max_grip_force_n
    if force_n is not None:
        if not math.isfinite(force_n) or force_n <= 0:
            raise PhysicalAttributeError("PHYSICAL_ATTRIBUTE_INVALID",
                                         f"requested grip force {force_n} is not a positive number")
        default_force = min(default_force, float(force_n))
    if ceiling is not None:
        default_force = min(default_force, ceiling)
    return GraspBudget(tolerance_m=tolerance, max_force_n=default_force,
                       close_rate_scale=rate, from_declaration=profile.declared,
                       material=profile.material)


def placement_half_height(category: str) -> float:
    """The commissioned half-height of a category, or a diagnosable refusal."""
    name = str(category or "").strip()
    height = PLACEMENT_HALF_HEIGHT_M.get(name)
    if height is None:
        raise PhysicalAttributeError(
            "PLACEMENT_PROFILE_UNAVAILABLE",
            f"no commissioned placement height for category {name!r}; "
            f"known categories: {', '.join(sorted(PLACEMENT_HALF_HEIGHT_M))}")
    return height
