// Validation and local-only state for the saved map explorer. This module stays
// free of DOM and WebGL so malformed historical artifacts can be rejected before
// they reach the renderer, and its behavior can be tested in Node.
(function (global) {
  "use strict";

  const ROOM_ALIASES = Object.freeze({
    living_room: "客厅", home_corridor: "走廊", kitchen: "厨房",
    bedroom: "卧室", bathroom: "卫生间",
  });
  const MAX_LOCAL_MARKS = 100;
  const LOCAL_MARK_RADIUS = .45;
  const ROOM_RADIUS = 1.2;
  const finiteVector = (value, length = 3) => Array.isArray(value)
    && value.length >= length && value.slice(0, length).every(Number.isFinite);

  function validateManifest(raw) {
    if (!raw || typeof raw !== "object" || !String(raw.mapId || "")) throw new Error("map identity is missing");
    if (raw.frameId !== "map") throw new Error("saved map frame must be map");
    if (!String(raw.calibrationRevision || "")) throw new Error("map calibration identity is missing");
    if (!finiteVector(raw.bounds?.min) || !finiteVector(raw.bounds?.max)) throw new Error("map bounds must be finite");
    if (raw.bounds.min.some((value, axis) => value > raw.bounds.max[axis])) throw new Error("map bounds are reversed");
    if (!Number.isFinite(raw.pointCount) || raw.pointCount < 0) throw new Error("map point count must be finite");
    if (!Number.isInteger(raw.lodLevels) || raw.lodLevels < 1) throw new Error("map LOD count is invalid");
    return raw;
  }

  function verifyArtifact(raw, map) {
    validateManifest(map);
    if (!raw || typeof raw !== "object") throw new Error("saved map artifact is not an object");
    if (raw.mapId !== undefined && raw.mapId !== map.mapId) throw new Error("artifact map identity does not match selection");
    if (raw.frameId !== undefined && raw.frameId !== "map") throw new Error("artifact frame does not match map");
    if (raw.calibrationRevision !== undefined && raw.calibrationRevision !== map.calibrationRevision) throw new Error("artifact calibration does not match map");
    const revision = raw.mapRevision ?? raw.hash;
    if (revision && map.hash && revision !== map.hash) throw new Error("artifact revision does not match map identity");
  }

  function positionOf(item) {
    const candidate = item?.position || item?.centroid || item?.target || item?.pose?.position || item?.pose;
    if (!finiteVector(candidate)) throw new Error("saved annotation position must contain finite XYZ values");
    return candidate.slice(0, 3).map(Number);
  }

  function normalizeSemantics(raw, map) {
    verifyArtifact(raw, map);
    const source = raw.annotations || raw.semantics || raw.workspaces || [];
    if (!Array.isArray(source)) throw new Error("saved annotations must be an array");
    return source.map((item, index) => {
      const id = String(item?.id || item?.workspaceId || item?.name || item?.label || `annotation-${index + 1}`);
      const original = String(item?.label || item?.name || id);
      const count = Number(item?.collectedPointCount ?? item?.pointCount ?? item?.sampleCount ?? 0);
      return Object.freeze({
        id, label: ROOM_ALIASES[original] || ROOM_ALIASES[id] || String(item?.aliases?.find(alias => /[\u3400-\u9fff]/.test(alias)) || original),
        sourceLabel: original, position: Object.freeze(positionOf(item)),
        collectedPointCount: Number.isFinite(count) && count >= 0 ? Math.floor(count) : 0,
      });
    });
  }

  function normalizeTrajectory(raw, map) {
    verifyArtifact(raw, map);
    const geoJSON = raw?.type === "FeatureCollection"
      ? raw.features?.find(feature => feature?.geometry?.type === "LineString")?.geometry?.coordinates : null;
    const source = raw.trajectory || raw.points || raw.poses || geoJSON || [];
    if (!Array.isArray(source)) throw new Error("saved trajectory must be an array");
    return source.map(item => {
      const candidate = Array.isArray(item) ? item : item?.position || item?.pose?.position || item?.pose;
      if (!Array.isArray(candidate) || candidate.length < 2 || !candidate.slice(0,3).every(Number.isFinite)) throw new Error("saved trajectory must contain finite XYZ samples");
      return Object.freeze([Number(candidate[0]),Number(candidate[1]),Number(candidate[2] || 0)]);
    });
  }

  function storageKey(map) {
    validateManifest(map);
    return `tangying.map-marks.v1:${encodeURIComponent(map.mapId)}:${encodeURIComponent(map.hash || map.revision || "unversioned")}`;
  }

  function focusPose(current, target, bounds) {
    if (!finiteVector(current?.position) || !finiteVector(current?.target) || !finiteVector(target)) throw new Error("focus pose needs finite coordinates");
    const direction=current.position.map((value,index)=>value-current.target[index]);
    const length=Math.hypot(...direction) || 1;
    const diagonal=finiteVector(bounds?.min) && finiteVector(bounds?.max)
      ? Math.hypot(...bounds.max.map((value,index)=>value-bounds.min[index])) : 8;
    const distance=Math.max(1.5,Math.min(3.5,diagonal*.24));
    const normalized=direction.map(value=>value/length);
    return Object.freeze({target:Object.freeze(target.slice(0,3).map(Number)),position:Object.freeze(target.slice(0,3).map((value,index)=>Number(value)+normalized[index]*distance))});
  }

  function loadLocalMarks(storage, map) {
    try {
      const rows = JSON.parse(storage?.getItem(storageKey(map)) || "[]");
      if (!Array.isArray(rows)) return [];
      return rows.filter(row => row && typeof row.id === "string" && typeof row.label === "string"
        && row.label.trim().length > 0 && row.label.length <= 80 && finiteVector(row.position)).slice(-MAX_LOCAL_MARKS);
    } catch (_) { return []; }
  }

  function writeMarks(storage, map, marks) {
    try { storage?.setItem(storageKey(map), JSON.stringify(marks.slice(-MAX_LOCAL_MARKS))); return true; }
    catch (_) { return false; }
  }

  function saveLocalMark(storage, map, candidate) {
    const label = String(candidate?.label || "").trim();
    if (!label) throw new Error("local mark label is required");
    if (label.length > 80) throw new Error("local mark label cannot exceed 80 characters");
    if (!finiteVector(candidate?.position)) throw new Error("local mark position must contain finite XYZ values");
    const mark = Object.freeze({
      id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`,
      label, position: Object.freeze(candidate.position.slice(0, 3).map(Number)),
      collectedPointCount: Number.isFinite(candidate.collectedPointCount) ? Math.max(0,Math.floor(candidate.collectedPointCount)) : null,
      createdAt: Date.now(),
    });
    const marks = [...loadLocalMarks(storage, map), mark];
    if (!writeMarks(storage, map, marks)) throw new Error("local mark storage is unavailable");
    return mark;
  }

  function deleteLocalMark(storage, map, id) {
    return writeMarks(storage, map, loadLocalMarks(storage, map).filter(mark => mark.id !== id));
  }

  function mapIdentity(map) {
    return map ? `${String(map.mapId || "")}\n${String(map.hash || map.revision || "")}` : "";
  }

  function bindPendingPick(picked, map, generation) {
    if (!finiteVector(picked?.position)) throw new Error("pending pick position must be finite");
    return Object.freeze({
      position: Object.freeze(picked.position.slice(0,3).map(Number)),
      collectedPointCount: Number.isFinite(picked.collectedPointCount) ? picked.collectedPointCount : null,
      mapIdentity: mapIdentity(map), generation,
    });
  }

  function pendingPickMatches(pending, map, generation) {
    return Boolean(pending && pending.generation === generation && pending.mapIdentity && pending.mapIdentity === mapIdentity(map));
  }

  function refreshLocalMarkCounts(marks, countNearby, cloudAvailable) {
    return (marks || []).map(mark => ({
      ...mark,
      collectedPointCount: cloudAvailable && typeof countNearby === "function"
        ? Math.max(0,Math.floor(Number(countNearby(mark.position,LOCAL_MARK_RADIUS)) || 0)) : null,
    }));
  }

  global.TangyingMapExplorer = Object.freeze({
    ROOM_ALIASES, validateManifest, normalizeSemantics, normalizeTrajectory,
    storageKey, loadLocalMarks, saveLocalMark, deleteLocalMark, focusPose, MAX_LOCAL_MARKS,
    mapIdentity, bindPendingPick, pendingPickMatches, refreshLocalMarkCounts,
    LOCAL_MARK_RADIUS, ROOM_RADIUS,
  });
})(globalThis);
