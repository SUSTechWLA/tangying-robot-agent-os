"""Verified map packages and semantic workspace lookup, with no activation side effect."""
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .map_manifest import load_manifest, verify_artifacts
from .workspace_planner import plan_workspace

#: How many directories below the root a map package may sit.
#:
#: One, because the reference scene ships its surveys under a group directory
#: (`artifacts/maps/furnished-home/<id>`) while a plain survey lands directly in the
#: root (`artifacts/maps/<id>`). Both are the same kind of package, and joining the
#: root and the id - which is what this used to do - found only the second kind. A
#: grouped map then failed to activate with "manifest.json does not exist" while
#: sitting right there on disk, which reads as a lost map rather than as a lookup
#: that never looked. `console/maps.go` carries the same depth for the same reason.
GROUP_DEPTH = 1


class MapCatalog:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def locate(self, map_id):
        """The directory holding a package with this id, or ``None``.

        Searched rather than joined, because with a group level in play the root is
        not the only place a package can be. The id still has to be one safe path
        segment: it is validated against the resolved path below, so a traversal
        attempt cannot name a directory outside the catalog.
        """

        if not isinstance(map_id, str) or not map_id or Path(map_id).name != map_id \
                or map_id in {".", ".."}:
            return None
        direct = (self.root / map_id)
        if (direct / "manifest.json").is_file():
            return direct.resolve()
        if GROUP_DEPTH < 1:
            return None
        try:
            groups = sorted(entry for entry in self.root.iterdir() if entry.is_dir())
        except OSError:
            return None
        for group in groups:
            if group.name in {".", ".."} or Path(group.name).name != group.name:
                continue
            candidate = group / map_id
            if (candidate / "manifest.json").is_file():
                return candidate.resolve()
        return None

    def open(self, map_id, *, robot_id, calibration_revision, map_revision=None):
        directory = self.locate(map_id)
        if directory is None:
            raise ValueError(f'no map package named {map_id!r} in the catalog')
        if self.root not in directory.parents:
            raise ValueError('map_id must identify a package within the catalog')
        manifest = load_manifest(directory)
        if (manifest['mapId'] != map_id or manifest['robotId'] != robot_id
                or manifest['frameId'] != 'map' or not calibration_revision
                or manifest['calibrationRevision'] != calibration_revision
                or (map_revision is not None and manifest['hash'] != map_revision)):
            raise ValueError('map, robot, frame or calibration revision mismatch')
        if any(not check.ok for check in verify_artifacts(manifest, directory)):
            raise ValueError('map artifact integrity check failed')
        return directory, manifest

    def plan(self, map_id, location_name, *, robot_id, calibration_revision,
             map_revision, start_xy, envelope, validate_candidate=None):
        if not isinstance(map_revision, str) or len(map_revision) != 64:
            raise ValueError("workspace planning requires an explicit active map revision")
        directory, manifest = self.open(map_id, robot_id=robot_id,
            calibration_revision=calibration_revision, map_revision=map_revision)
        artifacts = manifest['artifacts']
        def read(role):
            limits = {"navigation": 32768, "navigation_grid": 4100000, "semantics": 1000000}
            if artifacts[role]["bytes"] > limits[role]:
                raise ValueError(f"{role} exceeds the planning input budget")
            return (directory / artifacts[role]['href']).read_bytes()
        # All three artifacts are verified above and belong to this revision.
        semantics = json.loads(read('semantics'))
        if (semantics.get('schemaVersion') != 'map.semantics.v1'
                or semantics.get('mapId') != map_id
                or semantics.get('calibrationRevision') != calibration_revision
                or semantics.get('frameId') != 'map'):
            raise ValueError('semantic annotations do not belong to this map')
        wanted = location_name.strip().casefold()
        matches = [entry for entry in semantics['workspaces']
                   if wanted in [str(v).strip().casefold() for v in [entry['name'], *entry.get('aliases', [])]]]
        if len(matches) != 1:
            raise ValueError('workspace name is unknown or ambiguous; specify a registered name')
        grid = self.navigation_grid(directory, manifest)
        result = plan_workspace(grid,
            start_xy, matches[0]['target'], envelope, validate_candidate=validate_candidate)
        return {**result, 'mapId': map_id, 'mapRevision': manifest['hash'],
                'calibrationRevision': calibration_revision, 'workspace': matches[0]['name']}

    def recall_objects(self, map_id, *, robot_id, calibration_revision, category,
                       attributes=None, max_age_ms=900_000, now_unix_ms=None):
        """Where the active map last saw a category, newest sighting first.

        The map's object layer is evidence with timestamps, so this filters by age
        and reports the age of every hit. A map published before the layer existed
        answers "nothing recalled" rather than failing: that is a missing answer,
        not a broken map.
        """
        import time as _time

        directory, manifest = self.open(map_id, robot_id=robot_id,
                                        calibration_revision=calibration_revision)
        entry = (manifest.get('artifacts') or {}).get('objects')
        if not entry:
            return []
        if entry['bytes'] > 1_000_000:
            raise ValueError('object layer exceeds the recall budget')
        document = json.loads((directory / entry['href']).read_text())
        if (document.get('schemaVersion') != 'map.objects.v1' or document.get('mapId') != map_id
                or document.get('frameId') != 'map'
                or document.get('calibrationRevision') != calibration_revision):
            raise ValueError('object layer does not belong to this map')
        wanted = str(category or '').strip().casefold()
        if not wanted:
            raise ValueError('recall needs an object category')
        filters = {str(key).strip().casefold(): str(value).strip().casefold()
                   for key, value in dict(attributes or {}).items()}
        now = int(now_unix_ms) if isinstance(now_unix_ms, int) and now_unix_ms > 0 \
            else int(_time.time() * 1000)
        found = []
        for item in document.get('objects') or []:
            if str(item.get('category', '')).casefold() != wanted:
                continue
            item_attributes = {str(key).casefold(): str(value).casefold()
                               for key, value in dict(item.get('attributes') or {}).items()}
            if any(item_attributes.get(key) != value for key, value in filters.items()):
                continue
            age = now - int(item.get('lastSeenUnixMs') or 0)
            if age < 0:
                # A timestamp from the future is a clock disagreement, not a fresh
                # sighting; the observation cannot be trusted as newer than now.
                continue
            if age > max_age_ms:
                continue
            found.append({
                'id': item.get('id'), 'category': item.get('category'),
                'attributes': dict(item.get('attributes') or {}),
                'pose': list(item.get('pose') or []), 'frameId': 'map',
                'confidence': item.get('confidence'), 'sightings': item.get('sightings'),
                'lastSeenUnixMs': item.get('lastSeenUnixMs'), 'ageMs': age,
                'evidenceFrameId': item.get('evidenceFrameId'), 'mapId': map_id,
                'mapRevision': manifest['hash'],
            })
        found.sort(key=lambda item: item['ageMs'])
        return found

    @staticmethod
    def navigation_grid(directory, manifest):
        artifacts = manifest['artifacts']
        def read(role):
            maximum = {'navigation':32768,'navigation_grid':4100000}[role]
            if artifacts[role]['bytes'] > maximum:
                raise ValueError(f'{role} exceeds planning input budget')
            return (directory/artifacts[role]['href']).read_bytes()
        config = json.loads(read('navigation'))
        if (config.get('mode') != 'trinary' or config.get('negate') != 0
                or config.get('occupied_thresh') != .65 or config.get('free_thresh') != .196):
            raise ValueError('planner supports canonical trinary navigation packages only')
        declared_image = (directory / artifacts['navigation']['href']).parent / config.get('image', '')
        if declared_image.resolve() != (directory / artifacts['navigation_grid']['href']).resolve():
            raise ValueError('navigation YAML image does not match the verified grid artifact')
        with Image.open(io.BytesIO(read('navigation_grid'))) as image:
            if image.width * image.height > 250000 or image.mode != 'L':
                raise ValueError('navigation grid exceeds planner budget or is not 8-bit grayscale')
            pixels = np.asarray(image).copy()
        cells = np.flipud(np.where(pixels == 254, 0, np.where(pixels == 0, 100, -1)))
        from .navigation_map import validate_grid
        return validate_grid({'width': cells.shape[1], 'height': cells.shape[0],
            'origin': config['origin'], 'resolution': config['resolution'], 'cells': cells})
