import { AssetRegistry } from "./asset_registry.js";
import { CalibrationGuide } from "./calibration_guide.js";
import { MapCloudPoints } from "./map_cloud_points.js";
import { MapViewer } from "./map_viewer.js";
import { RobotModelInstance } from "./robot_model.js";
import { WebGLSceneRenderer } from "./webgl_scene_renderer.js";

globalThis.TangyingWebGL = Object.freeze({
  AssetRegistry,
  CalibrationGuide,
  MapCloudPoints,
  MapViewer,
  RobotModelInstance,
  WebGLSceneRenderer,
});
