import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const SCHEMA_VERSION = "tangying.visual-asset.v1";
const DEFAULT_SCENE_ID = "robocasa-handoff-v1";
const SHA256 = /^[a-f0-9]{64}$/;
const CANONICAL_JOINTS = Object.freeze([
  "joint.left.rotation", "joint.left.pitch", "joint.left.elbow",
  "joint.left.wrist_pitch", "joint.left.wrist_roll", "joint.left.jaw",
  "joint.right.rotation", "joint.right.pitch", "joint.right.elbow",
  "joint.right.wrist_pitch", "joint.right.wrist_roll", "joint.right.jaw",
  "joint.head.pan", "joint.head.tilt",
]);
const DEFAULT_LIMITS = Object.freeze({
  "scene.glb": 128 * 1024 * 1024,
  "xlerobot.glb": 64 * 1024 * 1024,
  "xlerobot.binding.json": 256 * 1024,
});

function visualError(code, detail) {
  const error = new Error(detail ? `${code}: ${detail}` : code);
  error.code = code;
  return error;
}

function asBytes(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  throw visualError("VISUAL_ASSET_RESPONSE_INVALID", "asset response is not binary data");
}

async function digestSHA256(bytes) {
  if (!globalThis.crypto?.subtle) {
    throw visualError("VISUAL_HASH_UNAVAILABLE", "Web Crypto SHA-256 is required");
  }
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function defaultFetchJSON(url) {
  const response = await fetch(url, { credentials: "same-origin", cache: "no-cache" });
  if (!response.ok) throw visualError("VISUAL_MANIFEST_FETCH_FAILED", `HTTP ${response.status}`);
  if (response.url && new URL(response.url).origin !== url.origin) {
    throw visualError("VISUAL_MANIFEST_REDIRECT", "cross-origin redirects are not allowed");
  }
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > 256 * 1024) {
    throw visualError("VISUAL_MANIFEST_TOO_LARGE", `${declaredLength} bytes`);
  }
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > 256 * 1024) {
    throw visualError("VISUAL_MANIFEST_TOO_LARGE", "manifest exceeds 256 KiB");
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw visualError("VISUAL_MANIFEST_INVALID", error.message);
  }
}

async function defaultFetchBytes(url, maximumBytes) {
  const response = await fetch(url, { credentials: "same-origin", cache: "default" });
  if (!response.ok) throw visualError("VISUAL_ASSET_FETCH_FAILED", `${url.pathname}: HTTP ${response.status}`);
  if (response.url && new URL(response.url).origin !== url.origin) {
    throw visualError("VISUAL_ASSET_REDIRECT", `${url.pathname}: cross-origin redirects are not allowed`);
  }
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maximumBytes) {
    throw visualError("VISUAL_ASSET_TOO_LARGE", `${url.pathname}: ${declaredLength} bytes`);
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > maximumBytes) {
    throw visualError("VISUAL_ASSET_TOO_LARGE", `${url.pathname}: ${bytes.byteLength} bytes`);
  }
  return bytes;
}

function assertSelfContainedGLB(bytes, assetName) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.byteLength < 20 || view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2) {
    throw visualError("VISUAL_GLB_INVALID", `${assetName}: invalid GLB header`);
  }
  if (view.getUint32(8, true) !== bytes.byteLength) {
    throw visualError("VISUAL_GLB_INVALID", `${assetName}: inconsistent GLB length`);
  }
  let offset = 12;
  let document = null;
  while (offset + 8 <= bytes.byteLength) {
    const length = view.getUint32(offset, true);
    const type = view.getUint32(offset + 4, true);
    offset += 8;
    if (length > bytes.byteLength - offset) {
      throw visualError("VISUAL_GLB_INVALID", `${assetName}: invalid chunk length`);
    }
    if (type === 0x4e4f534a && document === null) {
      const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(offset, offset + length)).trim();
      try {
        document = JSON.parse(text);
      } catch (error) {
        throw visualError("VISUAL_GLB_INVALID", `${assetName}: ${error.message}`);
      }
    }
    offset += length;
  }
  if (!document) throw visualError("VISUAL_GLB_INVALID", `${assetName}: JSON chunk missing`);
  const references = [...(document.buffers || []), ...(document.images || [])]
    .map((entry) => entry?.uri)
    .filter((uri) => typeof uri === "string" && !uri.startsWith("data:"));
  if (references.length) {
    throw visualError("VISUAL_GLB_EXTERNAL_RESOURCE", `${assetName}: external URI is not allowed`);
  }
}

function defaultLoadGLB(bytes, url) {
  assertSelfContainedGLB(bytes, url.pathname.split("/").pop());
  const loader = new GLTFLoader();
  return new Promise((resolve, reject) => {
    loader.parse(
      bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
      new URL("./", url).href,
      resolve,
      (error) => reject(visualError("VISUAL_GLB_PARSE_FAILED", error?.message || String(error))),
    );
  });
}

function authoritativeIdentity(snapshot) {
  let identity = null;
  for (const entity of Object.values(snapshot?.entities || {})) {
    const attributes = entity?.attributes;
    const modelHash = attributes?.model_hash ?? attributes?.modelHash;
    const sceneId = attributes?.scene_id ?? attributes?.sceneId;
    if (modelHash === undefined && sceneId === undefined) continue;
    if (!SHA256.test(modelHash || "") || typeof sceneId !== "string"
      || !sceneId || sceneId.trim() !== sceneId) {
      throw visualError(
        "VISUAL_MODEL_IDENTITY_INVALID",
        "every authoritative identity requires a scene ID and lowercase SHA-256 model hash",
      );
    }
    if (identity && (identity.modelHash !== modelHash || identity.sceneId !== sceneId)) {
      throw visualError("VISUAL_MODEL_IDENTITY_CONFLICT", "authoritative entities disagree on visual model identity");
    }
    identity = { modelHash, sceneId };
  }
  if (!identity) {
    throw visualError("VISUAL_MODEL_IDENTITY_MISSING", "authoritative scene_id/model_hash attributes are required");
  }
  return identity;
}

function validateBinding(binding) {
  if (!binding || typeof binding !== "object" || Array.isArray(binding)) {
    throw visualError("VISUAL_BINDING_INVALID", "binding must be an object");
  }
  const keys = Object.keys(binding);
  if (keys.length !== CANONICAL_JOINTS.length || CANONICAL_JOINTS.some((key) => !keys.includes(key))) {
    throw visualError("VISUAL_BINDING_INVALID", "binding must contain exactly the 14 canonical joints");
  }
  for (const key of CANONICAL_JOINTS) {
    const value = binding[key];
    const scalars = [value?.direction, value?.offset, value?.minimum, value?.maximum];
    const mappedEndpoints = [
      value?.minimum * value?.direction + value?.offset,
      value?.maximum * value?.direction + value?.offset,
    ];
    if (typeof value?.node !== "string" || !value.node || !Array.isArray(value.axis)
      || value.axis.length !== 3 || !value.axis.every(Number.isFinite)
      || Math.hypot(...value.axis) <= 1e-12 || !scalars.every(Number.isFinite)
      || Math.abs(value.direction) !== 1 || value.minimum > value.maximum
      || !mappedEndpoints.every(Number.isFinite)) {
      throw visualError("VISUAL_BINDING_INVALID", `invalid binding for ${key}`);
    }
  }
  return binding;
}

export class AssetRegistry {
  constructor(options = {}) {
    const manifestReference = options.manifestURL || "/assets/scenes/robocasa-handoff-v1/manifest.json";
    const baseURL = options.baseURL || globalThis.location?.href;
    try {
      this.manifestURL = baseURL ? new URL(manifestReference, baseURL) : new URL(manifestReference);
    } catch (_) {
      throw visualError("VISUAL_MANIFEST_URL_INVALID", "an absolute manifest URL or browser location is required");
    }
    if (baseURL && this.manifestURL.origin !== new URL(baseURL).origin) {
      throw visualError("VISUAL_MANIFEST_URL_INVALID", "manifest must use the configured local origin");
    }
    this.expectedSceneId = options.sceneId || DEFAULT_SCENE_ID;
    this.fetchJSON = options.fetchJSON || defaultFetchJSON;
    this.fetchBytes = options.fetchBytes || defaultFetchBytes;
    this.loadGLB = options.loadGLB || defaultLoadGLB;
    this.assetLimits = { ...DEFAULT_LIMITS, ...(options.assetLimits || {}) };
    this.manifestPromise = null;
    this.bundlePromises = new Map();
  }

  async load(snapshot) {
    const identity = authoritativeIdentity(snapshot);
    const manifest = await this.#manifest();
    if (identity.sceneId !== manifest.sceneId || identity.modelHash !== manifest.modelHash) {
      throw visualError(
        "VISUAL_MODEL_MISMATCH",
        `authoritative ${identity.sceneId}/${identity.modelHash} does not match ${manifest.sceneId}/${manifest.modelHash}`,
      );
    }
    if (!this.bundlePromises.has(manifest.modelHash)) {
      const pending = this.#loadBundle(manifest).catch((error) => {
        this.bundlePromises.delete(manifest.modelHash);
        throw error;
      });
      this.bundlePromises.set(manifest.modelHash, pending);
    }
    return this.bundlePromises.get(manifest.modelHash);
  }

  async #manifest() {
    if (!this.manifestPromise) {
      this.manifestPromise = Promise.resolve()
        .then(() => this.fetchJSON(this.manifestURL))
        .then((value) => this.#validateManifest(value))
        .catch((error) => {
          this.manifestPromise = null;
          throw error;
        });
    }
    return this.manifestPromise;
  }

  #validateManifest(manifest) {
    if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)
      || manifest.schemaVersion !== SCHEMA_VERSION
      || manifest.sceneId !== this.expectedSceneId
      || !SHA256.test(manifest.modelHash || "")
      || manifest.worldFrame !== "world" || manifest.upAxis !== "Z" || manifest.units !== "meter"
      || !manifest.robotModels?.xlerobot || !manifest.contentHashes) {
      throw visualError("VISUAL_MANIFEST_INVALID", "unsupported or incomplete visual manifest");
    }
    const specifications = [
      ["scene.glb", manifest.sceneAsset],
      ["xlerobot.glb", manifest.robotModels.xlerobot.asset],
      ["xlerobot.binding.json", manifest.robotModels.xlerobot.binding],
    ];
    const assets = {};
    for (const [name, reference] of specifications) {
      const expectedHash = manifest.contentHashes[name];
      if (!SHA256.test(expectedHash || "")) {
        throw visualError("VISUAL_MANIFEST_INVALID", `missing lowercase SHA-256 for ${name}`);
      }
      assets[name] = this.#resolveAssetURL(name, reference, expectedHash);
    }
    return Object.freeze({ ...manifest, assets: Object.freeze(assets) });
  }

  #resolveAssetURL(name, reference, expectedHash) {
    if (typeof reference !== "string" || !reference || reference.startsWith("/")
      || reference.startsWith("//") || reference.includes("\\") || reference.includes("#")) {
      throw visualError("VISUAL_ASSET_URL_INVALID", `${name}: URL must be local and relative`);
    }
    let resolved;
    try {
      resolved = new URL(reference, this.manifestURL);
    } catch (_) {
      throw visualError("VISUAL_ASSET_URL_INVALID", `${name}: malformed URL`);
    }
    const directory = new URL("./", this.manifestURL);
    const queryKeys = [...resolved.searchParams.keys()];
    if (resolved.origin !== this.manifestURL.origin || !resolved.pathname.startsWith(directory.pathname)
      || resolved.pathname !== `${directory.pathname}${name}` || queryKeys.length !== 1
      || queryKeys[0] !== "v" || resolved.searchParams.get("v") !== expectedHash) {
      throw visualError("VISUAL_ASSET_URL_INVALID", `${name}: path or version hash mismatch`);
    }
    return resolved;
  }

  async #verifiedBytes(name, url, expectedHash) {
    const bytes = asBytes(await this.fetchBytes(url, this.assetLimits[name]));
    if (bytes.byteLength > this.assetLimits[name]) {
      throw visualError("VISUAL_ASSET_TOO_LARGE", `${name}: ${bytes.byteLength} bytes`);
    }
    const actualHash = await digestSHA256(bytes);
    if (actualHash !== expectedHash) {
      throw visualError("VISUAL_CONTENT_HASH_MISMATCH", `${name}: expected ${expectedHash}, got ${actualHash}`);
    }
    return bytes;
  }

  async #loadBundle(manifest) {
    const hashes = manifest.contentHashes;
    const [sceneBytes, robotBytes, bindingBytes] = await Promise.all([
      this.#verifiedBytes("scene.glb", manifest.assets["scene.glb"], hashes["scene.glb"]),
      this.#verifiedBytes("xlerobot.glb", manifest.assets["xlerobot.glb"], hashes["xlerobot.glb"]),
      this.#verifiedBytes(
        "xlerobot.binding.json",
        manifest.assets["xlerobot.binding.json"],
        hashes["xlerobot.binding.json"],
      ),
    ]);
    let binding;
    try {
      binding = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bindingBytes));
    } catch (error) {
      throw visualError("VISUAL_BINDING_INVALID", error.message);
    }
    validateBinding(binding);
    const [sceneGLTF, robotGLTF] = await Promise.all([
      this.loadGLB(sceneBytes, manifest.assets["scene.glb"]),
      this.loadGLB(robotBytes, manifest.assets["xlerobot.glb"]),
    ]);
    if (!sceneGLTF?.scene || !robotGLTF?.scene) {
      throw visualError("VISUAL_GLB_INVALID", "GLTFLoader result is missing a scene root");
    }
    if (typeof robotGLTF.scene.getObjectByName !== "function") {
      throw visualError("VISUAL_GLB_INVALID", "robot scene root is not an Object3D");
    }
    for (const [name, entry] of Object.entries(binding)) {
      if (!robotGLTF.scene.getObjectByName(entry.node)) {
        throw visualError("VISUAL_BINDING_NODE_MISSING", `${name}: ${entry.node}`);
      }
    }
    return Object.freeze({
      manifest,
      modelHash: manifest.modelHash,
      scene: sceneGLTF.scene,
      robotTemplate: robotGLTF.scene,
      binding: Object.freeze(binding),
    });
  }
}

export { CANONICAL_JOINTS, authoritativeIdentity, validateBinding };
