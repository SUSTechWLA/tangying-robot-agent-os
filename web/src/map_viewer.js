import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { MapCloudPoints } from "./map_cloud_points.js";

// A saved SLAM map has its own coordinate frame. Keep it in a dedicated scene
// rather than overlaying map XYZ onto an unrelated simulator world.
export class MapViewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({canvas, antialias:true});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x101b2b);
    this.camera = new THREE.PerspectiveCamera(48, 1, .02, 1000);
    this.camera.up.set(0,0,1);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.addEventListener("change", () => { this.draw(); this.onChange?.(this.camera.position); });
    this.points = new Map();
    this.resize = new ResizeObserver(() => this.draw());
    this.resize.observe(canvas);
  }
  fit(bounds) {
    const low = bounds?.min || [-1,-1,0], high = bounds?.max || [1,1,2];
    const centre = new THREE.Vector3(...low).add(new THREE.Vector3(...high)).multiplyScalar(.5);
    const radius = Math.max(new THREE.Vector3(...high).distanceTo(new THREE.Vector3(...low)), 1);
    const direction = new THREE.Vector3(.55,-.75,1).normalize();
    const right = new THREE.Vector3().crossVectors(this.camera.up,direction).normalize();
    const up = new THREE.Vector3().crossVectors(direction,right).normalize();
    const aspect = Math.max(this.canvas.clientWidth / Math.max(this.canvas.clientHeight,1),.25);
    const tangent = Math.tan(THREE.MathUtils.degToRad(this.camera.fov/2));
    let distance = 1;
    for (const x of [low[0],high[0]]) for (const y of [low[1],high[1]]) for (const z of [low[2],high[2]]) {
      const offset = new THREE.Vector3(x,y,z).sub(centre);
      distance = Math.max(distance, offset.dot(direction)+Math.abs(offset.dot(right))/(tangent*aspect),
        offset.dot(direction)+Math.abs(offset.dot(up))/tangent);
    }
    this.controls.target.copy(centre);
    this.camera.position.copy(centre).add(direction.multiplyScalar(distance*1.12));
    this.camera.far = Math.max(100, radius * 20);
    this.controls.update();
    this.draw();
  }
  show(level, geometry) {
    this.hide(level);
    // Coarse levels represent larger voxels. Keep them legible at the initial
    // whole-map scale instead of drawing a nearly invisible subpixel cloud.
    const points = MapCloudPoints.create(geometry,{size:Math.max(.025,.32/(2**level))});
    this.points.set(level, points);
    this.scene.add(points);
    this.draw();
  }
  hide(level) { MapCloudPoints.dispose(this.points.get(level)); this.points.delete(level); this.draw(); }
  draw() {
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    if (!width || !height) return;
    const finest = Math.max(...this.points.keys());
    for (const [level, points] of this.points) points.visible = level === finest;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
  }
  dispose() {
    this.onChange = null;
    for (const level of [...this.points.keys()]) this.hide(level);
    this.controls.dispose(); this.resize.disconnect(); this.renderer.dispose();
  }
}
