import { AssetRegistry } from "./asset_registry.js";
import { MapCloudPoints } from "./map_cloud_points.js";
import { RobotModelInstance } from "./robot_model.js";
import { WebGLSceneRenderer } from "./webgl_scene_renderer.js";

globalThis.TangyingWebGL = Object.freeze({
  AssetRegistry,
  MapCloudPoints,
  RobotModelInstance,
  WebGLSceneRenderer,
});
