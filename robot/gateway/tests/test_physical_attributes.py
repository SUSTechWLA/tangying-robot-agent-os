"""Physical properties of objects: the vocabulary, the budget, and the refusal.

The point of this axis is that "how to grasp" stops being a code constant. The
tests below hold the three properties that make that safe: an object that declares
nothing behaves exactly as the commissioned workcell did, a declaration is honoured
and recorded, and a declaration the system cannot understand is refused with a
reason instead of silently falling back to a default that might be wrong.
"""

from __future__ import annotations

import pytest
from tangying_robot_gateway.physical_attributes import (
    DEFAULT_MAX_FORCE_N,
    DEFAULT_TOLERANCE_M,
    MATERIALS,
    PhysicalAttributeError,
    grasp_budget,
    physical_profile,
    placement_half_height,
)


def test_an_object_that_declares_nothing_gets_the_commissioned_behaviour():
    for attributes in ({}, None, {"color": "white"}, {"material": "unknown"}):
        profile = physical_profile(attributes)
        budget = grasp_budget(profile)
        assert budget.tolerance_m == DEFAULT_TOLERANCE_M
        assert budget.max_force_n == DEFAULT_MAX_FORCE_N
        assert budget.close_rate_scale == 1.0
        assert budget.from_declaration is False
        assert budget.material == "unknown"


def test_the_commissioned_placement_heights_are_unchanged():
    # These are the literals the driver used to index directly. The vocabulary
    # moved them into data; it must not have moved their values.
    assert placement_half_height("cup") == 0.06
    assert placement_half_height("bottle") == 0.08


def test_a_fragile_declaration_tightens_the_grasp_and_says_so():
    profile = physical_profile({"material": "fragile"})
    assert profile.identified is True
    budget = grasp_budget(profile)
    assert budget.tolerance_m < DEFAULT_TOLERANCE_M
    assert budget.max_force_n < DEFAULT_MAX_FORCE_N
    assert budget.close_rate_scale < 1.0
    assert budget.from_declaration is True
    assert budget.as_dict()["material"] == "fragile"
    # Soft and deformable are treated differently from fragile and from rigid.
    budgets = {name: grasp_budget(physical_profile({"material": name})) for name in MATERIALS}
    assert budgets["soft"].max_force_n > budgets["fragile"].max_force_n
    assert budgets["rigid"].max_force_n == DEFAULT_MAX_FORCE_N
    assert budgets["deformable"].close_rate_scale < budgets["rigid"].close_rate_scale


def test_an_object_stating_its_own_limit_wins_over_the_material_default():
    declared = grasp_budget(physical_profile({"material": "rigid", "max_grip_force_n": "3.5"}))
    assert declared.max_force_n == 3.5
    # A matter-of-fact request cannot raise it either.
    capped = grasp_budget(physical_profile({"material": "rigid"}), force_n=100.0)
    assert capped.max_force_n == DEFAULT_MAX_FORCE_N
    lowered = grasp_budget(physical_profile({"material": "rigid"}), force_n=5.0)
    assert lowered.max_force_n == 5.0


def test_mass_is_read_from_the_string_transport():
    profile = physical_profile({"mass_g": "180"})
    assert profile.mass_g == 180 and profile.declared is True


def test_a_declaration_the_system_cannot_understand_is_refused_loudly():
    for attributes, code in (
        ({"material": "marshmallow"}, "PHYSICAL_ATTRIBUTE_UNKNOWN"),
        ({"mass_g": "heavy"}, "PHYSICAL_ATTRIBUTE_INVALID"),
        ({"mass_g": "-5"}, "PHYSICAL_ATTRIBUTE_INVALID"),
        ({"mass_g": "inf"}, "PHYSICAL_ATTRIBUTE_INVALID"),
        ({"max_grip_force_n": "0"}, "PHYSICAL_ATTRIBUTE_INVALID"),
        ({"max_grip_force_n": "1000"}, "PHYSICAL_ATTRIBUTE_INVALID"),
        ("not-a-mapping", "PHYSICAL_ATTRIBUTES_INVALID"),
    ):
        with pytest.raises(PhysicalAttributeError) as raised:
            physical_profile(attributes)
        assert raised.value.code == code, (attributes, raised.value.code)


def test_an_unknown_category_is_refused_with_the_known_set():
    with pytest.raises(PhysicalAttributeError) as raised:
        placement_half_height("teapot")
    assert raised.value.code == "PLACEMENT_PROFILE_UNAVAILABLE"
    assert "teapot" in str(raised.value) and "cup" in str(raised.value)
    with pytest.raises(PhysicalAttributeError):
        placement_half_height("")


def test_a_requested_force_must_be_a_positive_number():
    with pytest.raises(PhysicalAttributeError):
        grasp_budget(physical_profile({}), force_n=0.0)
    with pytest.raises(PhysicalAttributeError):
        grasp_budget(physical_profile({}), force_n=float("nan"))
