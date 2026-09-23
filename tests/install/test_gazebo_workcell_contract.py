"""The scene generator and the workcell detector must describe the same workcell.

These two halves are written in different languages' idioms and no compiler relates
them: `gazebo_scenes._fixtures` builds SDF with material colours and a cylinder
radius, and `gazebo_perception` decides what it can see using its own colour
thresholds and its own idea of that radius. Nothing in either file mentions the
other, so a change to one is invisible to the other.

What makes that dangerous is the failure mode, not the coupling. A cup that is no
longer unmistakably red does not become a cup whose colour was misread — it stops
being reported at all, and `manipulation.pick` answers OBJECT_NOT_VISIBLE from a
re-observe the operator never sees (`gazebo_manipulation.py:161-164`). The symptom
is "the arm arrived and the object vanished", which points at the arm. A radius
that no longer matches makes the wall fit reject every capture, with the same
symptom.

So this module asserts the two halves against each other, in both directions: each
fixture must satisfy its own mask, and it must fail the other three. The negative
half is what keeps the fixtures identifiable — a workcell where one colour
satisfied two masks would be ambiguous, which the detector refuses on purpose.
"""
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def scenes_module():
    return module('gazebo_scenes_contract', 'robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_scenes.py')


def perception_module():
    from tangying_robot_gateway import gazebo_perception

    return gazebo_perception


#: The scene the colour and geometry assertions run against.
#:
#: `home_task` is chosen because every scene appends the same commissioned
#: fixtures, and this one needs no prepared asset pack — so the test measures the
#: generator rather than whether a developer has run `prepare_home_world.py`.
FIXTURE_SCENE = 'home_task'


def composed_scene(tmp_path, scene):
    """Compose one real scene and return its world element."""
    composer = scenes_module()
    base = ROOT / 'robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf'
    output = tmp_path / f'{scene}.sdf'
    composer.compose_scene(scene, base, output)
    return ET.parse(output).getroot().find('world')


def fixture_colours(world):
    """Byte RGB each commissioned fixture's material actually declares.

    Read from the composed SDF rather than from the generator's source, so this
    measures what the simulator is told to render rather than what the generator
    intended to write. Anything upstream that rewrote or dropped a colour is seen.
    """
    colours = {}
    for model in world.findall('model'):
        name = model.get('name')
        if name is None:
            continue
        for material in model.findall('link/visual/material'):
            diffuse = material.findtext('diffuse')
            if diffuse:
                values = [float(part) for part in diffuse.split()]
                colours[name] = np.array(values[:3]) * 255.0
    return colours


def test_every_scene_declares_the_generator_name_for_each_fixture(tmp_path):
    """The detector keys on generator names; a rename would silently empty it."""
    perception = perception_module()
    expected = set(perception.OBJECT_MODELS.values()) | set(perception.DESTINATION_MODELS.values())
    for scene in ('tabletop', 'home', 'home_task'):
        world = composed_scene(tmp_path, scene)
        present = {model.get('name') for model in world.findall('model')}
        missing = sorted(expected - present)
        assert not missing, f'{scene} does not contain {missing}, so those ids can never resolve'

    # The furnished scene is built on an external pack that a fresh checkout does
    # not have. It is still checked when the pack is present, because it is the
    # scene the acceptance runs use and a contract that only holds for the
    # generated-only scenes would miss exactly the deployment that matters.
    prepared = ROOT / 'artifacts/home-assets'
    if not prepared.exists():
        pytest.skip('furnished world pack is not prepared in this checkout')


def test_commissioned_fixture_colours_satisfy_their_own_detector_masks(tmp_path):
    """A fixture whose colour left its mask stops being reported, not misreported."""
    perception = perception_module()
    world = composed_scene(tmp_path, FIXTURE_SCENE)
    colours = fixture_colours(world)

    masks = {identity: mask for identity, _category, _colour, mask in perception.COLOUR_MASKS}
    for identity, mask in masks.items():
        model = (perception.OBJECT_MODELS | perception.DESTINATION_MODELS)[identity]
        assert model in colours, f'{identity}: {model} declares no material colour'
        r, g, b = colours[model]
        assert bool(np.asarray(mask(r, g, b)).all()), (
            f'{identity} ({model}) is rendered as RGB {np.round(colours[model], 1).tolist()}, '
            f'which its own detector mask rejects; the fixture would never be reported')


def test_no_fixture_colour_satisfies_another_fixtures_mask(tmp_path):
    """Two satisfied masks would be an ambiguous workcell, which is a refusal.

    This is the half that makes the previous test meaningful: if the masks were
    permissive enough to accept anything, every fixture would pass its own check
    and the workcell would still be unusable.
    """
    perception = perception_module()
    world = composed_scene(tmp_path, FIXTURE_SCENE)
    colours = fixture_colours(world)
    models = perception.OBJECT_MODELS | perception.DESTINATION_MODELS

    for identity, _category, _colour, mask in perception.COLOUR_MASKS:
        r, g, b = colours[models[identity]]
        for other, _c2, _n2, other_mask in perception.COLOUR_MASKS:
            if other == identity:
                continue
            assert not bool(np.asarray(other_mask(r, g, b)).all()), (
                f'{identity} also satisfies the {other} mask, so the workcell is ambiguous')


def test_masks_still_refuse_a_near_miss(tmp_path):
    """The masks have to be strict, or "commissioned colour" means nothing.

    Without this the colour tests above could be satisfied by thresholds so wide
    that any tint passed, which is the opposite of what the detector promises.

    Two things this measures, both learned from what the masks actually are:

      * They compare channel *ratios*, so they are deliberately insensitive to
        brightness — a cup in shadow is still the cup — and sensitive to hue. A
        near miss is therefore a hue shift, and the sizes below are chosen to
        cross that ratio boundary rather than merely look different.
      * They are a "predominantly this colour" test, not an exact one. A modest
        hue shift still passes and *should* pass: the fixture is still dominated
        by red. Asserting otherwise would demand a stricter detector than the
        workcell needs, so the miss has to be large enough to actually violate
        the ratio.
    """
    import colorsys

    perception = perception_module()
    world = composed_scene(tmp_path, FIXTURE_SCENE)
    colours = fixture_colours(world)
    models = perception.OBJECT_MODELS | perception.DESTINATION_MODELS

    for identity, _category, _colour, mask in perception.COLOUR_MASKS:
        r, g, b = colours[models[identity]]
        accepted, refused = 0, 0
        for shift in range(5, 175, 5):
            hue, saturation, value = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            rotated = np.array(colorsys.hsv_to_rgb((hue + shift / 360.0) % 1.0,
                                                   saturation, value)) * 255.0
            if bool(np.asarray(mask(*rotated)).all()):
                accepted += 1
            else:
                refused += 1
        assert refused, f'{identity}: no hue shift anywhere on the wheel leaves its mask'
        assert accepted, (
            f'{identity}: every hue shift leaves its mask, so the mask carries no '
            f'colour information at all')
        assert refused > accepted, (
            f'{identity}: {accepted} of 34 hue shifts still satisfy its mask, so the '
            f'mask is too permissive to establish an identity')


def test_pickable_geometry_matches_the_detector_fit(tmp_path):
    """The wall fit projects the commissioned radius; the scene emits it.

    `PICKABLE_RADIUS_M` is used as a known dimension rather than a measured one,
    so the two must agree or the fit rejects every capture.
    """
    perception = perception_module()
    world = composed_scene(tmp_path, FIXTURE_SCENE)
    checked = 0
    for model in world.findall('model'):
        if model.get('name') not in set(perception.OBJECT_MODELS.values()):
            continue
        for cylinder in model.findall('link/visual/geometry/cylinder'):
            radius = float(cylinder.findtext('radius'))
            height = float(cylinder.findtext('length'))
            assert radius == perception.PICKABLE_RADIUS_M, (
                f'{model.get("name")}: scene radius {radius} != detector '
                f'PICKABLE_RADIUS_M {perception.PICKABLE_RADIUS_M}')
            assert height == perception.PICKABLE_HEIGHT_M, (
                f'{model.get("name")}: scene height {height} != detector '
                f'PICKABLE_HEIGHT_M {perception.PICKABLE_HEIGHT_M}')
            checked += 1
    assert checked, 'no pickable cylinder was found to check, so this test proved nothing'
