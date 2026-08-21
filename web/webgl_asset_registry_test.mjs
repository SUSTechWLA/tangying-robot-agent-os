import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { AssetRegistry } from "./src/asset_registry.js";

const encoder = new TextEncoder();
const sceneBytes = encoder.encode("scene glb fixture");
const robotBytes = encoder.encode("robot glb fixture");
const canonicalJoints = [
  "joint.left.rotation", "joint.left.pitch", "joint.left.elbow",
  "joint.left.wrist_pitch", "joint.left.wrist_roll", "joint.left.jaw",
  "joint.right.rotation", "joint.right.pitch", "joint.right.elbow",
  "joint.right.wrist_pitch", "joint.right.wrist_roll", "joint.right.jaw",
  "joint.head.pan", "joint.head.tilt",
];
const binding = Object.fromEntries(canonicalJoints.map((name, index) => [name, {
  node: `node-${index}`, axis: [1, 0, 0], direction: index % 2 ? -1 : 1,
  offset: 0, minimum: -1, maximum: 1,
}]));
const bindingBytes = encoder.encode(JSON.stringify(binding));
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

function manifest(modelHash = "a".repeat(64), overrides = {}) {
  const hashes = {
    "scene.glb": sha256(sceneBytes),
    "xlerobot.glb": sha256(robotBytes),
    "xlerobot.binding.json": sha256(bindingBytes),
  };
  return {
    schemaVersion: "tangying.visual-asset.v1",
    sceneId: "robocasa-handoff-v1",
    modelHash,
    worldFrame: "world",
    upAxis: "Z",
    units: "meter",
    sceneAsset: `scene.glb?v=${hashes["scene.glb"]}`,
    robotModels: {
      xlerobot: {
        asset: `xlerobot.glb?v=${hashes["xlerobot.glb"]}`,
        binding: `xlerobot.binding.json?v=${hashes["xlerobot.binding.json"]}`,
      },
    },
    contentHashes: hashes,
    licenses: [],
    ...overrides,
  };
}

function snapshot(modelHash = "a".repeat(64), sceneId = "robocasa-handoff-v1") {
  return {
    revision: 7,
    entities: {
      kitchen: {
        entityId: "kitchen",
        attributes: { static: "true", model_hash: modelHash, scene_id: sceneId },
      },
    },
  };
}

function successfulRegistry(options = {}) {
  const calls = { manifest: 0, assets: [], glbs: [] };
  const registry = new AssetRegistry({
    manifestURL: "https://console.test/assets/scenes/robocasa-handoff-v1/manifest.json",
    fetchJSON: async () => { calls.manifest += 1; return manifest(); },
    fetchBytes: async (url) => {
      calls.assets.push(url.href);
      if (url.pathname.endsWith("scene.glb")) return sceneBytes;
      if (url.pathname.endsWith("xlerobot.glb")) return robotBytes;
      if (url.pathname.endsWith("xlerobot.binding.json")) return bindingBytes;
      throw new Error(`unexpected asset ${url.href}`);
    },
    loadGLB: async (bytes, url) => {
      calls.glbs.push(url.href);
      return {
        scene: {
          asset: url.pathname,
          bytes: bytes.byteLength,
          getObjectByName: () => ({}),
        },
      };
    },
    ...options,
  });
  return { registry, calls };
}

test("asset registry rejects an authoritative model mismatch before loading assets", async () => {
  let assetLoads = 0;
  const registry = new AssetRegistry({
    manifestURL: "https://console.test/assets/scenes/robocasa-handoff-v1/manifest.json",
    fetchJSON: async () => manifest("a".repeat(64)),
    fetchBytes: async () => { assetLoads += 1; return sceneBytes; },
    loadGLB: async () => ({}),
  });

  await assert.rejects(() => registry.load(snapshot("b".repeat(64))), /VISUAL_MODEL_MISMATCH/);
  assert.equal(assetLoads, 0);
});

test("asset registry rejects a manifest URL outside the configured local origin", () => {
  assert.throws(() => new AssetRegistry({
    baseURL: "https://console.test/fleet/",
    manifestURL: "https://evil.test/manifest.json",
  }), /VISUAL_MANIFEST_URL_INVALID/);
});

test("default manifest loading rejects a cross-origin redirect", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    url: "https://evil.test/manifest.json",
    headers: new Headers(),
    text: async () => JSON.stringify(manifest()),
  });
  try {
    const registry = new AssetRegistry({
      manifestURL: "https://console.test/assets/scenes/robocasa-handoff-v1/manifest.json",
      loadGLB: async () => ({}),
    });
    await assert.rejects(() => registry.load(snapshot()), /VISUAL_MANIFEST_REDIRECT/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("asset registry validates schema, fixed scene identity, and hash-addressed local URLs", async () => {
  const invalidManifests = [
    manifest(undefined, { schemaVersion: "tangying.visual-asset.v2" }),
    manifest(undefined, { sceneId: "another-scene" }),
    manifest(undefined, { sceneAsset: "https://evil.test/scene.glb?v=" + sha256(sceneBytes) }),
    manifest(undefined, { sceneAsset: "../scene.glb?v=" + sha256(sceneBytes) }),
    manifest(undefined, { sceneAsset: "scene.glb?v=" + "A".repeat(64) }),
    manifest(undefined, { sceneAsset: "scene.glb?v=" + "0".repeat(64) }),
  ];

  for (const candidate of invalidManifests) {
    const registry = new AssetRegistry({
      manifestURL: "https://console.test/assets/scenes/robocasa-handoff-v1/manifest.json",
      fetchJSON: async () => candidate,
      fetchBytes: async () => sceneBytes,
      loadGLB: async () => ({}),
    });
    await assert.rejects(() => registry.load(snapshot()), /VISUAL_(?:MANIFEST_INVALID|ASSET_URL_INVALID)/);
  }
});

test("asset registry verifies response bytes against manifest content hashes", async () => {
  const { registry } = successfulRegistry({
    fetchBytes: async (url) => url.pathname.endsWith("scene.glb")
      ? encoder.encode("tampered")
      : url.pathname.endsWith("xlerobot.glb") ? robotBytes : bindingBytes,
  });

  await assert.rejects(() => registry.load(snapshot()), /VISUAL_CONTENT_HASH_MISMATCH/);
});

test("asset registry rejects an incomplete canonical joint binding", async () => {
  const incomplete = encoder.encode(JSON.stringify({ "joint.left.pitch": binding["joint.left.pitch"] }));
  const candidate = manifest();
  candidate.contentHashes["xlerobot.binding.json"] = sha256(incomplete);
  candidate.robotModels.xlerobot.binding = `xlerobot.binding.json?v=${sha256(incomplete)}`;
  const { registry } = successfulRegistry({
    fetchJSON: async () => candidate,
    fetchBytes: async (url) => url.pathname.endsWith("scene.glb")
      ? sceneBytes
      : url.pathname.endsWith("xlerobot.glb") ? robotBytes : incomplete,
  });
  await assert.rejects(() => registry.load(snapshot()), /VISUAL_BINDING_INVALID/);
});

test("asset registry rejects bindings that name absent GLB nodes", async () => {
  const { registry } = successfulRegistry({
    loadGLB: async (_bytes, url) => ({
      scene: {
        asset: url.pathname,
        getObjectByName: () => url.pathname.endsWith("xlerobot.glb") ? null : {},
      },
    }),
  });
  await assert.rejects(() => registry.load(snapshot()), /VISUAL_BINDING_NODE_MISSING/);
});

test("asset registry loads one verified bundle once and returns the cached object", async () => {
  const { registry, calls } = successfulRegistry();

  const [first, second] = await Promise.all([registry.load(snapshot()), registry.load(snapshot())]);

  assert.equal(first, second);
  assert.equal(calls.manifest, 1);
  assert.equal(calls.assets.length, 3);
  assert.equal(calls.glbs.length, 2);
  assert.deepEqual(first.binding, binding);
  assert.equal(first.modelHash, "a".repeat(64));
  assert.match(first.scene.asset, /scene\.glb$/);
  assert.match(first.robotTemplate.asset, /xlerobot\.glb$/);
});
