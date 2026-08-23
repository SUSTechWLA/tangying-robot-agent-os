"""Build the self-contained browser asset bundle for the RoboCasa handoff scene."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from .composer import ComposedScene, SceneConfig
from .gltf_export import build_xlerobot_binding, export_mjcf_visual

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_VERSION = "tangying.visual-asset.v1"
_DYNAMIC_BODY_ROOTS = frozenset({"robot-1__chassis", "robot-2__chassis", "red-block"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_LICENSE_FILES = ("LICENSE-XLeRobot", "PROVENANCE-XLeRobot.md", "LICENSE-RoboCasa")
_CC_BY_4_LEGAL_CODE = """\
Attribution 4.0 International

=======================================================================

Creative Commons Corporation ("Creative Commons") is not a law firm and
does not provide legal services or legal advice. Distribution of
Creative Commons public licenses does not create a lawyer-client or
other relationship. Creative Commons makes its licenses and related
information available on an "as-is" basis. Creative Commons gives no
warranties regarding its licenses, any material licensed under their
terms and conditions, or any related information. Creative Commons
disclaims all liability for damages resulting from their use to the
fullest extent possible.

Using Creative Commons Public Licenses

Creative Commons public licenses provide a standard set of terms and
conditions that creators and other rights holders may use to share
original works of authorship and other material subject to copyright
and certain other rights specified in the public license below. The
following considerations are for informational purposes only, are not
exhaustive, and do not form part of our licenses.

     Considerations for licensors: Our public licenses are
     intended for use by those authorized to give the public
     permission to use material in ways otherwise restricted by
     copyright and certain other rights. Our licenses are
     irrevocable. Licensors should read and understand the terms
     and conditions of the license they choose before applying it.
     Licensors should also secure all rights necessary before
     applying our licenses so that the public can reuse the
     material as expected. Licensors should clearly mark any
     material not subject to the license. This includes other CC-
     licensed material, or material used under an exception or
     limitation to copyright. More considerations for licensors:
    wiki.creativecommons.org/Considerations_for_licensors

     Considerations for the public: By using one of our public
     licenses, a licensor grants the public permission to use the
     licensed material under specified terms and conditions. If
     the licensor's permission is not necessary for any reason--for
     example, because of any applicable exception or limitation to
     copyright--then that use is not regulated by the license. Our
     licenses grant only permissions under copyright and certain
     other rights that a licensor has authority to grant. Use of
     the licensed material may still be restricted for other
     reasons, including because others have copyright or other
     rights in the material. A licensor may make special requests,
     such as asking that all changes be marked or described.
     Although not required by our licenses, you are encouraged to
     respect those requests where reasonable. More considerations
     for the public:
    wiki.creativecommons.org/Considerations_for_licensees

=======================================================================

Creative Commons Attribution 4.0 International Public License

By exercising the Licensed Rights (defined below), You accept and agree
to be bound by the terms and conditions of this Creative Commons
Attribution 4.0 International Public License ("Public License"). To the
extent this Public License may be interpreted as a contract, You are
granted the Licensed Rights in consideration of Your acceptance of
these terms and conditions, and the Licensor grants You such rights in
consideration of benefits the Licensor receives from making the
Licensed Material available under these terms and conditions.


Section 1 -- Definitions.

  a. Adapted Material means material subject to Copyright and Similar
     Rights that is derived from or based upon the Licensed Material
     and in which the Licensed Material is translated, altered,
     arranged, transformed, or otherwise modified in a manner requiring
     permission under the Copyright and Similar Rights held by the
     Licensor. For purposes of this Public License, where the Licensed
     Material is a musical work, performance, or sound recording,
     Adapted Material is always produced where the Licensed Material is
     synched in timed relation with a moving image.

  b. Adapter's License means the license You apply to Your Copyright
     and Similar Rights in Your contributions to Adapted Material in
     accordance with the terms and conditions of this Public License.

  c. Copyright and Similar Rights means copyright and/or similar rights
     closely related to copyright including, without limitation,
     performance, broadcast, sound recording, and Sui Generis Database
     Rights, without regard to how the rights are labeled or
     categorized. For purposes of this Public License, the rights
     specified in Section 2(b)(1)-(2) are not Copyright and Similar
     Rights.

  d. Effective Technological Measures means those measures that, in the
     absence of proper authority, may not be circumvented under laws
     fulfilling obligations under Article 11 of the WIPO Copyright
     Treaty adopted on December 20, 1996, and/or similar international
     agreements.

  e. Exceptions and Limitations means fair use, fair dealing, and/or
     any other exception or limitation to Copyright and Similar Rights
     that applies to Your use of the Licensed Material.

  f. Licensed Material means the artistic or literary work, database,
     or other material to which the Licensor applied this Public
     License.

  g. Licensed Rights means the rights granted to You subject to the
     terms and conditions of this Public License, which are limited to
     all Copyright and Similar Rights that apply to Your use of the
     Licensed Material and that the Licensor has authority to license.

  h. Licensor means the individual(s) or entity(ies) granting rights
     under this Public License.

  i. Share means to provide material to the public by any means or
     process that requires permission under the Licensed Rights, such
     as reproduction, public display, public performance, distribution,
     dissemination, communication, or importation, and to make material
     available to the public including in ways that members of the
     public may access the material from a place and at a time
     individually chosen by them.

  j. Sui Generis Database Rights means rights other than copyright
     resulting from Directive 96/9/EC of the European Parliament and of
     the Council of 11 March 1996 on the legal protection of databases,
     as amended and/or succeeded, as well as other essentially
     equivalent rights anywhere in the world.

  k. You means the individual or entity exercising the Licensed Rights
     under this Public License. Your has a corresponding meaning.


Section 2 -- Scope.

  a. License grant.

       1. Subject to the terms and conditions of this Public License,
          the Licensor hereby grants You a worldwide, royalty-free,
          non-sublicensable, non-exclusive, irrevocable license to
          exercise the Licensed Rights in the Licensed Material to:

            a. reproduce and Share the Licensed Material, in whole or
               in part; and

            b. produce, reproduce, and Share Adapted Material.

       2. Exceptions and Limitations. For the avoidance of doubt, where
          Exceptions and Limitations apply to Your use, this Public
          License does not apply, and You do not need to comply with
          its terms and conditions.

       3. Term. The term of this Public License is specified in Section
          6(a).

       4. Media and formats; technical modifications allowed. The
          Licensor authorizes You to exercise the Licensed Rights in
          all media and formats whether now known or hereafter created,
          and to make technical modifications necessary to do so. The
          Licensor waives and/or agrees not to assert any right or
          authority to forbid You from making technical modifications
          necessary to exercise the Licensed Rights, including
          technical modifications necessary to circumvent Effective
          Technological Measures. For purposes of this Public License,
          simply making modifications authorized by this Section 2(a)
          (4) never produces Adapted Material.

       5. Downstream recipients.

            a. Offer from the Licensor -- Licensed Material. Every
               recipient of the Licensed Material automatically
               receives an offer from the Licensor to exercise the
               Licensed Rights under the terms and conditions of this
               Public License.

            b. No downstream restrictions. You may not offer or impose
               any additional or different terms or conditions on, or
               apply any Effective Technological Measures to, the
               Licensed Material if doing so restricts exercise of the
               Licensed Rights by any recipient of the Licensed
               Material.

       6. No endorsement. Nothing in this Public License constitutes or
          may be construed as permission to assert or imply that You
          are, or that Your use of the Licensed Material is, connected
          with, or sponsored, endorsed, or granted official status by,
          the Licensor or others designated to receive attribution as
          provided in Section 3(a)(1)(A)(i).

  b. Other rights.

       1. Moral rights, such as the right of integrity, are not
          licensed under this Public License, nor are publicity,
          privacy, and/or other similar personality rights; however, to
          the extent possible, the Licensor waives and/or agrees not to
          assert any such rights held by the Licensor to the limited
          extent necessary to allow You to exercise the Licensed
          Rights, but not otherwise.

       2. Patent and trademark rights are not licensed under this
          Public License.

       3. To the extent possible, the Licensor waives any right to
          collect royalties from You for the exercise of the Licensed
          Rights, whether directly or through a collecting society
          under any voluntary or waivable statutory or compulsory
          licensing scheme. In all other cases the Licensor expressly
          reserves any right to collect such royalties.


Section 3 -- License Conditions.

Your exercise of the Licensed Rights is expressly made subject to the
following conditions.

  a. Attribution.

       1. If You Share the Licensed Material (including in modified
          form), You must:

            a. retain the following if it is supplied by the Licensor
               with the Licensed Material:

                 i. identification of the creator(s) of the Licensed
                    Material and any others designated to receive
                    attribution, in any reasonable manner requested by
                    the Licensor (including by pseudonym if
                    designated);

                ii. a copyright notice;

               iii. a notice that refers to this Public License;

                iv. a notice that refers to the disclaimer of
                    warranties;

                 v. a URI or hyperlink to the Licensed Material to the
                    extent reasonably practicable;

            b. indicate if You modified the Licensed Material and
               retain an indication of any previous modifications; and

            c. indicate the Licensed Material is licensed under this
               Public License, and include the text of, or the URI or
               hyperlink to, this Public License.

       2. You may satisfy the conditions in Section 3(a)(1) in any
          reasonable manner based on the medium, means, and context in
          which You Share the Licensed Material. For example, it may be
          reasonable to satisfy the conditions by providing a URI or
          hyperlink to a resource that includes the required
          information.

       3. If requested by the Licensor, You must remove any of the
          information required by Section 3(a)(1)(A) to the extent
          reasonably practicable.

       4. If You Share Adapted Material You produce, the Adapter's
          License You apply must not prevent recipients of the Adapted
          Material from complying with this Public License.


Section 4 -- Sui Generis Database Rights.

Where the Licensed Rights include Sui Generis Database Rights that
apply to Your use of the Licensed Material:

  a. for the avoidance of doubt, Section 2(a)(1) grants You the right
     to extract, reuse, reproduce, and Share all or a substantial
     portion of the contents of the database;

  b. if You include all or a substantial portion of the database
     contents in a database in which You have Sui Generis Database
     Rights, then the database in which You have Sui Generis Database
     Rights (but not its individual contents) is Adapted Material; and

  c. You must comply with the conditions in Section 3(a) if You Share
     all or a substantial portion of the contents of the database.

For the avoidance of doubt, this Section 4 supplements and does not
replace Your obligations under this Public License where the Licensed
Rights include other Copyright and Similar Rights.


Section 5 -- Disclaimer of Warranties and Limitation of Liability.

  a. UNLESS OTHERWISE SEPARATELY UNDERTAKEN BY THE LICENSOR, TO THE
     EXTENT POSSIBLE, THE LICENSOR OFFERS THE LICENSED MATERIAL AS-IS
     AND AS-AVAILABLE, AND MAKES NO REPRESENTATIONS OR WARRANTIES OF
     ANY KIND CONCERNING THE LICENSED MATERIAL, WHETHER EXPRESS,
     IMPLIED, STATUTORY, OR OTHER. THIS INCLUDES, WITHOUT LIMITATION,
     WARRANTIES OF TITLE, MERCHANTABILITY, FITNESS FOR A PARTICULAR
     PURPOSE, NON-INFRINGEMENT, ABSENCE OF LATENT OR OTHER DEFECTS,
     ACCURACY, OR THE PRESENCE OR ABSENCE OF ERRORS, WHETHER OR NOT
     KNOWN OR DISCOVERABLE. WHERE DISCLAIMERS OF WARRANTIES ARE NOT
     ALLOWED IN FULL OR IN PART, THIS DISCLAIMER MAY NOT APPLY TO YOU.

  b. TO THE EXTENT POSSIBLE, IN NO EVENT WILL THE LICENSOR BE LIABLE
     TO YOU ON ANY LEGAL THEORY (INCLUDING, WITHOUT LIMITATION,
     NEGLIGENCE) OR OTHERWISE FOR ANY DIRECT, SPECIAL, INDIRECT,
     INCIDENTAL, CONSEQUENTIAL, PUNITIVE, EXEMPLARY, OR OTHER LOSSES,
     COSTS, EXPENSES, OR DAMAGES ARISING OUT OF THIS PUBLIC LICENSE OR
     USE OF THE LICENSED MATERIAL, EVEN IF THE LICENSOR HAS BEEN
     ADVISED OF THE POSSIBILITY OF SUCH LOSSES, COSTS, EXPENSES, OR
     DAMAGES. WHERE A LIMITATION OF LIABILITY IS NOT ALLOWED IN FULL OR
     IN PART, THIS LIMITATION MAY NOT APPLY TO YOU.

  c. The disclaimer of warranties and limitation of liability provided
     above shall be interpreted in a manner that, to the extent
     possible, most closely approximates an absolute disclaimer and
     waiver of all liability.


Section 6 -- Term and Termination.

  a. This Public License applies for the term of the Copyright and
     Similar Rights licensed here. However, if You fail to comply with
     this Public License, then Your rights under this Public License
     terminate automatically.

  b. Where Your right to use the Licensed Material has terminated under
     Section 6(a), it reinstates:

       1. automatically as of the date the violation is cured, provided
          it is cured within 30 days of Your discovery of the
          violation; or

       2. upon express reinstatement by the Licensor.

     For the avoidance of doubt, this Section 6(b) does not affect any
     right the Licensor may have to seek remedies for Your violations
     of this Public License.

  c. For the avoidance of doubt, the Licensor may also offer the
     Licensed Material under separate terms or conditions or stop
     distributing the Licensed Material at any time; however, doing so
     will not terminate this Public License.

  d. Sections 1, 5, 6, 7, and 8 survive termination of this Public
     License.


Section 7 -- Other Terms and Conditions.

  a. The Licensor shall not be bound by any additional or different
     terms or conditions communicated by You unless expressly agreed.

  b. Any arrangements, understandings, or agreements regarding the
     Licensed Material not stated herein are separate from and
     independent of the terms and conditions of this Public License.


Section 8 -- Interpretation.

  a. For the avoidance of doubt, this Public License does not, and
     shall not be interpreted to, reduce, limit, restrict, or impose
     conditions on any use of the Licensed Material that could lawfully
     be made without permission under this Public License.

  b. To the extent possible, if any provision of this Public License is
     deemed unenforceable, it shall be automatically reformed to the
     minimum extent necessary to make it enforceable. If the provision
     cannot be reformed, it shall be severed from this Public License
     without affecting the enforceability of the remaining terms and
     conditions.

  c. No term or condition of this Public License will be waived and no
     failure to comply consented to unless expressly agreed to by the
     Licensor.

  d. Nothing in this Public License constitutes or may be interpreted
     as a limitation upon, or waiver of, any privileges and immunities
     that apply to the Licensor or You, including from the legal
     processes of any jurisdiction or authority.


=======================================================================

Creative Commons is not a party to its public
licenses. Notwithstanding, Creative Commons may elect to apply one of
its public licenses to material it publishes and in those instances
will be considered the “Licensor.” The text of the Creative Commons
public licenses is dedicated to the public domain under the CC0 Public
Domain Dedication. Except for the limited purpose of indicating that
material is shared under a Creative Commons public license or as
otherwise permitted by the Creative Commons policies published at
creativecommons.org/policies, Creative Commons does not authorize the
use of the trademark "Creative Commons" or any other trademark or logo
of Creative Commons without its prior written consent including,
without limitation, in connection with any unauthorized modifications
to any of its public licenses or any other arrangements,
understandings, or agreements concerning use of licensed material. For
the avoidance of doubt, this paragraph does not form part of the
public licenses.

Creative Commons may be contacted at creativecommons.org.
"""


@dataclass(frozen=True, slots=True)
class VisualAssetManifest:
    schema_version: str
    scene_id: str
    model_hash: str
    world_frame: str
    up_axis: str
    units: str
    scene_asset: str
    robot_models: dict[str, dict[str, str]]
    content_hashes: dict[str, str]
    licenses: tuple[str, ...]


def with_hash_query(file_name: str, sha256: str) -> str:
    """Return a stable local asset path with an immutable content-hash query."""

    if _SHA256_PATTERN.fullmatch(sha256) is None:
        raise ValueError("asset digest must be a 64-character lowercase SHA-256")
    return f"{file_name}?v={sha256}"


def _write_json(path: Path, payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _load_xlerobot() -> ET.Element:
    source = Path(SceneConfig().robot_mjcf).resolve()
    root = ET.parse(source).getroot()
    compiler = root.find("compiler")
    mesh_dir = Path(compiler.get("meshdir", ".")) if compiler is not None else Path(".")
    texture_dir = Path(compiler.get("texturedir", ".")) if compiler is not None else Path(".")
    for element in root.iter():
        file_name = element.get("file")
        if not file_name or Path(file_name).is_absolute():
            continue
        directory = (
            mesh_dir
            if element.tag == "mesh"
            else texture_dir
            if element.tag == "texture"
            else Path(".")
        )
        element.set("file", str((source.parent / directory / file_name).resolve()))
    return root


def _robocasa_root() -> Path:
    spec = importlib.util.find_spec("robocasa")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("RoboCasa must be installed to export its browser assets")
    package_dir = Path(next(iter(spec.submodule_search_locations))).resolve()
    return package_dir.parent


def _normalized_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def _robocasa_notice() -> str:
    root = _robocasa_root()
    license_path = root / "LICENSE"
    readme_path = root / "README.md"
    asset_readme_path = root / "robocasa/models/assets/README.md"
    for path in (license_path, readme_path, asset_readme_path):
        if not path.is_file():
            raise FileNotFoundError(f"RoboCasa notice source does not exist: {path}")

    readme = readme_path.read_text(encoding="utf-8")
    cc_claim = (
        "Assets and Datasets: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.en)"
    )
    if cc_claim not in readme:
        raise ValueError("RoboCasa README no longer declares assets and datasets as CC BY 4.0")

    asset_source = (
        "https://huggingface.co/datasets/nvidia/"
        "PhysicalAI-Robotics-Manipulation-Objects-Kitchen-MJCF"
    )
    asset_readme = asset_readme_path.read_text(encoding="utf-8")
    if asset_source not in asset_readme:
        raise ValueError("RoboCasa asset README no longer identifies the NVIDIA source dataset")

    header = f"""RoboCasa code and asset notice
================================

This aggregate notice applies to the RoboCasa-derived contents embedded in
scene.glb in this directory.

RoboCasa code
-------------

RoboCasa version: {version("robocasa")}
Source: https://github.com/robocasa/robocasa
License: MIT License. The complete upstream code license follows below.

RoboCasa assets and datasets
----------------------------

The RoboCasa README for this source version designates "Assets and Datasets"
as Creative Commons Attribution 4.0 International (CC BY 4.0):
https://creativecommons.org/licenses/by/4.0/deed.en

Attribution/source as supplied by the downloaded RoboCasa asset README:
{asset_source}

The complete CC BY 4.0 legal code is included below for offline distribution.
Official legal code source:
https://creativecommons.org/licenses/by/4.0/legalcode.txt

TangYing export changes: selected visual fixture meshes, primitives, materials,
and textures from RoboCasa layout 1 / style 1 were converted from the active
MJCF scene into a self-contained GLB. The two XLeRobot body subtrees and the
dynamic red block were omitted from scene.glb and are instantiated separately
at runtime. This notice does not imply endorsement by RoboCasa, NVIDIA, or
Creative Commons.

--- RoboCasa code: MIT License ---

{license_path.read_text(encoding="utf-8").rstrip()}

--- RoboCasa assets and datasets: CC BY 4.0 legal code ---

{_CC_BY_4_LEGAL_CODE.rstrip()}
"""
    return _normalized_text(header)


def _copy_licenses(output_dir: Path) -> None:
    robot_assets = _PROJECT_ROOT / "sim/mujoco/assets/xlerobot"
    sources = {
        "LICENSE-XLeRobot": robot_assets / "LICENSE",
        "PROVENANCE-XLeRobot.md": robot_assets / "PROVENANCE.md",
    }
    for destination, source in sources.items():
        normalized = _normalized_text(source.read_text(encoding="utf-8"))
        (output_dir / destination).write_bytes(normalized.encode("utf-8"))
    (output_dir / "LICENSE-RoboCasa").write_bytes(_robocasa_notice().encode("utf-8"))


def export_visual_bundle(scene: ComposedScene, output_dir: Path) -> VisualAssetManifest:
    """Export one deterministic static scene and reusable articulated robot bundle."""

    if scene.scene_id != "robocasa-handoff-v1":
        raise ValueError(f"unsupported visual scene: {scene.scene_id!r}")
    if _SHA256_PATTERN.fullmatch(scene.model_hash) is None:
        raise ValueError("scene model_hash must be a 64-character lowercase SHA-256")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scene_root = ET.fromstring(scene.xml)
    static_export = export_mjcf_visual(
        scene_root,
        output_dir / "scene.glb",
        lambda body_name: body_name not in _DYNAMIC_BODY_ROOTS,
    )

    robot_root = _load_xlerobot()
    robot_export = export_mjcf_visual(
        robot_root,
        output_dir / "xlerobot.glb",
        lambda _body_name: True,
    )
    binding_payload = {
        canonical: asdict(binding)
        for canonical, binding in build_xlerobot_binding(robot_root).items()
    }
    binding_sha = _write_json(output_dir / "xlerobot.binding.json", binding_payload)

    content_hashes = {
        "scene.glb": static_export.sha256,
        "xlerobot.binding.json": binding_sha,
        "xlerobot.glb": robot_export.sha256,
    }
    robot_models = {
        "xlerobot": {
            "asset": with_hash_query("xlerobot.glb", robot_export.sha256),
            "binding": with_hash_query("xlerobot.binding.json", binding_sha),
        }
    }
    manifest = VisualAssetManifest(
        schema_version=_SCHEMA_VERSION,
        scene_id=scene.scene_id,
        model_hash=scene.model_hash,
        world_frame="world",
        up_axis="Z",
        units="meter",
        scene_asset=with_hash_query("scene.glb", static_export.sha256),
        robot_models=robot_models,
        content_hashes=content_hashes,
        licenses=_LICENSE_FILES,
    )
    _copy_licenses(output_dir)
    _write_json(
        output_dir / "manifest.json",
        {
            "schemaVersion": manifest.schema_version,
            "sceneId": manifest.scene_id,
            "modelHash": manifest.model_hash,
            "worldFrame": manifest.world_frame,
            "upAxis": manifest.up_axis,
            "units": manifest.units,
            "sceneAsset": manifest.scene_asset,
            "robotModels": manifest.robot_models,
            "contentHashes": manifest.content_hashes,
            "licenses": list(manifest.licenses),
        },
    )
    return manifest
