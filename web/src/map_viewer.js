import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { MapCloudPoints } from "./map_cloud_points.js";

export function candidatePointIndices(count, limit = 20000) {
  const stride=Math.max(1,Math.ceil(Math.max(0,count)/Math.max(1,limit)));
  const result=[];
  for(let index=0;index<count;index+=stride) result.push(index);
  return result;
}

// A saved SLAM map has its own coordinate frame. Keep it in a dedicated scene
// rather than overlaying map XYZ onto an unrelated simulator world.
export class MapViewer {
  constructor(canvas, {overlay = null} = {}) {
    this.canvas = canvas;
    this.overlay = overlay;
    this.renderer = new THREE.WebGLRenderer({canvas, antialias:true});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0d2421);
    this.camera = new THREE.PerspectiveCamera(48, 1, .02, 1000);
    this.camera.up.set(0,0,1);
    this.controls = new OrbitControls(this.camera, canvas);
    this.handleChange = () => { this.draw(); this.onChange?.(this.camera.position); };
    this.controls.addEventListener("change", this.handleChange);
    this.points = new Map();
    this.sourceGeometry = new Map();
    this.bounds = null;
    this.colorMode = "height";
    this.pointSize = .04;
    this.annotationNodes = [];
    this.trajectory = null;
    this.keyframes = [];
    this.keyframeGroup = null;
    this.keyframesVisible = true;
    this.keyframePickEnabled = true;
    this.keyframePointerDown = event => { this.pointerStart = event.button === 0 ? [event.clientX,event.clientY] : null; };
    this.keyframePointerUp = event => {
      if (this.keyframePickEnabled && event.button === 0 && this.pointerStart && Math.hypot(event.clientX-this.pointerStart[0],event.clientY-this.pointerStart[1]) < 5) {
        const frame=this.pickKeyframe(event.clientX,event.clientY);
        if(frame)this.onKeyframe?.(frame);
      }
      this.pointerStart=null;
    };
    canvas.addEventListener("pointerdown",this.keyframePointerDown);
    canvas.addEventListener("pointerup",this.keyframePointerUp);
    this.resize = new ResizeObserver(() => this.draw());
    this.resize.observe(canvas);
  }
  fit(bounds) {
    this.bounds = bounds;
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
  preset(name) {
    const low = this.bounds?.min || [-1,-1,0], high = this.bounds?.max || [1,1,2];
    const centre = new THREE.Vector3(...low).add(new THREE.Vector3(...high)).multiplyScalar(.5);
    const radius = Math.max(new THREE.Vector3(...high).distanceTo(new THREE.Vector3(...low)), 1);
    const directions = {top:new THREE.Vector3(0,0,1), isometric:new THREE.Vector3(.55,-.75,1).normalize()};
    if (name === "full") return this.fit(this.bounds);
    const direction = directions[name] || directions.isometric;
    this.controls.target.copy(centre);
    this.camera.position.copy(centre).add(direction.multiplyScalar(radius * 1.25));
    this.controls.update(); this.draw();
  }
  focus(position) {
    if (!Array.isArray(position) || !position.slice(0,3).every(Number.isFinite)) return;
    const current={position:this.camera.position.toArray(),target:this.controls.target.toArray()};
    const pose=globalThis.TangyingMapExplorer?.focusPose?.(current,position,this.bounds);
    const target = new THREE.Vector3(...position.slice(0,3));
    if (pose) this.camera.position.set(...pose.position);
    else {
      const offset=this.camera.position.clone().sub(this.controls.target).normalize().multiplyScalar(2);
      this.camera.position.copy(target).add(offset);
    }
    this.controls.target.copy(target);
    this.controls.update(); this.draw();
  }
  show(level, geometry) {
    this.hide(level);
    // Coarse levels represent larger voxels. Keep them legible at the initial
    // whole-map scale instead of drawing a nearly invisible subpixel cloud.
    this.sourceGeometry.set(level, geometry);
    const points = MapCloudPoints.create(geometry,{size:Math.max(this.pointSize,.32/(2**level)),colorMode:this.colorMode,bounds:this.bounds});
    this.points.set(level, points);
    this.scene.add(points);
    this.draw();
  }
  hide(level) { MapCloudPoints.dispose(this.points.get(level)); this.points.delete(level); this.sourceGeometry.delete(level); this.draw(); }
  setColorMode(mode) {
    this.colorMode = mode === "rgb" ? "rgb" : "height";
    for (const [level, geometry] of [...this.sourceGeometry]) {
      const points = this.points.get(level); if (points) { this.scene.remove(points); MapCloudPoints.dispose(points); }
      const replacement = MapCloudPoints.create(geometry,{size:Math.max(this.pointSize,.32/(2**level)),colorMode:this.colorMode,bounds:this.bounds});
      this.points.set(level,replacement); this.scene.add(replacement);
    }
    this.draw();
  }
  setPointSize(size) {
    this.pointSize = Math.max(.008, Math.min(.08, Number(size) || .025));
    for (const [level, points] of this.points) points.material.size = Math.max(this.pointSize,.32/(2**level));
    this.draw();
  }
  setTrajectory(samples, visible = true) {
    if (this.trajectory) { this.scene.remove(this.trajectory); this.trajectory.geometry.dispose(); this.trajectory.material.dispose(); }
    this.trajectory = null;
    if (!Array.isArray(samples) || samples.length < 2) return this.draw();
    const geometry = new THREE.BufferGeometry().setFromPoints(samples.map(sample => new THREE.Vector3(...sample)));
    this.trajectory = new THREE.Line(geometry,new THREE.LineBasicMaterial({color:0x087f76,transparent:true,opacity:.88}));
    this.trajectory.visible = visible; this.scene.add(this.trajectory); this.draw();
  }
  showTrajectory(visible) { if (this.trajectory) this.trajectory.visible = Boolean(visible); this.draw(); }
  setAnnotations(annotations, onFocus) {
    this.annotationNodes.forEach(({node}) => node.remove()); this.annotationNodes = [];
    if (!this.overlay) return;
    for (const annotation of annotations || []) {
      const node = document.createElement("button"); node.type = "button"; node.className = "map-projected-tag";
      const sampleText = Number.isFinite(annotation.collectedPointCount)
        ? (annotation.collectedPointCount > 0 ? ` · ${annotation.collectedPointCount} 点` : " · 无附近样本")
        : " · 样本数待加载";
      node.textContent = annotation.label + sampleText; node.setAttribute("aria-label", `聚焦 ${annotation.label}${sampleText}`);
      const handler = () => { this.focus(annotation.position); onFocus?.(annotation); };
      node.addEventListener("click",handler); this.overlay.append(node);
      this.annotationNodes.push({node,handler,annotation});
    }
    this.draw();
  }
  setKeyframes(frames, onSelect) {
    if(this.keyframeGroup) {
      this.scene.remove(this.keyframeGroup);
      this.keyframeGroup.traverse(node=>{node.geometry?.dispose();node.material?.dispose();});
    }
    this.keyframes=frames || []; this.onKeyframe=onSelect; this.keyframeGroup=null; this.keyframePoints=null;
    if(!this.keyframes.length)return this.draw();
    const group=new THREE.Group(), positions=[], directions=[],colors=[];
    for(const frame of this.keyframes) {
      const [x,y,z]=frame.position, yaw=frame.optimizedPose[2];
      positions.push(x,y,z+.06);
      const tip=[x+Math.cos(yaw)*.25,y+Math.sin(yaw)*.25,z+.06];
      directions.push(x,y,z+.06,...tip);
      for(const angle of [-.5,.5]) directions.push(...tip,tip[0]-Math.cos(yaw+angle)*.08,tip[1]-Math.sin(yaw+angle)*.08,z+.06);
      const color=new THREE.Color(frame.hasLoop?0xf5bb64:0x80d6cc);colors.push(color.r,color.g,color.b);
    }
    const geometry=new THREE.BufferGeometry();geometry.setAttribute("position",new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute("color",new THREE.Float32BufferAttribute(colors,3));
    this.keyframePoints=new THREE.Points(geometry,new THREE.PointsMaterial({size:7,sizeAttenuation:false,vertexColors:true,depthTest:false}));
    const arrows=new THREE.BufferGeometry();arrows.setAttribute("position",new THREE.Float32BufferAttribute(directions,3));
    group.add(this.keyframePoints,new THREE.LineSegments(arrows,new THREE.LineBasicMaterial({color:0xa5dad3,transparent:true,opacity:.65,depthTest:false})));
    group.renderOrder=5;group.visible=this.keyframesVisible;this.keyframeGroup=group;this.scene.add(group);this.draw();
  }
  showKeyframes(visible) {this.keyframesVisible=Boolean(visible);if(this.keyframeGroup)this.keyframeGroup.visible=this.keyframesVisible;this.draw();}
  selectKeyframe(id) {
    const colors=this.keyframePoints?.geometry.getAttribute("color");
    if(colors)for(const [index,frame] of this.keyframes.entries()) {
      const color=new THREE.Color(frame.frameId===id?0xffffff:frame.hasLoop?0xf5bb64:0x80d6cc);colors.setXYZ(index,color.r,color.g,color.b);
    }
    if(colors)colors.needsUpdate=true;this.draw();
  }
  pickKeyframe(clientX,clientY) {
    if(!this.keyframesVisible)return null;
    const rect=this.canvas.getBoundingClientRect();let best=null;
    for(const frame of this.keyframes) {
      const p=new THREE.Vector3(...frame.position);p.z+=.06;p.project(this.camera);
      const distance=Math.hypot(rect.left+(p.x+1)*rect.width/2-clientX,rect.top+(1-p.y)*rect.height/2-clientY);
      if(p.z>=-1&&p.z<=1&&distance<12&&(!best||distance<best.distance))best={frame,distance};
    }
    return best?.frame || null;
  }
  countNearby(position, radius = .28) {
    if (!Array.isArray(position)) return 0;
    let count = 0;
    for (const points of this.points.values()) if (points.visible) {
      const positions=points.geometry.getAttribute("position");
      for(let index=0;index<positions.count;index+=1) {
        if(Math.hypot(positions.getX(index)-position[0],positions.getY(index)-position[1],positions.getZ(index)-position[2])<=radius) count+=1;
      }
    }
    return count;
  }
  displayedPointCount() {
    for (const points of this.points.values()) if (points.visible) return points.geometry.getAttribute("position")?.count || 0;
    return 0;
  }
  pick(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    let best = null;
    for (const points of this.points.values()) {
      if (!points.visible) continue;
      const positions = points.geometry.getAttribute("position");
      const world=new THREE.Vector3();
      for (const index of candidatePointIndices(positions.count)) {
        world.fromBufferAttribute(positions,index).project(this.camera);
        const x = rect.left + (world.x + 1) * rect.width / 2;
        const y = rect.top + (1 - world.y) * rect.height / 2;
        const distance = Math.hypot(x-clientX,y-clientY);
        if (world.z >= -1 && world.z <= 1 && distance < 18 && (!best || distance < best.distance)) best={distance,position:[positions.getX(index),positions.getY(index),positions.getZ(index)]};
      }
    }
    if (!best) return null;
    return {position:best.position,collectedPointCount:this.countNearby(best.position,globalThis.TangyingMapExplorer?.LOCAL_MARK_RADIUS || .45)};
  }
  draw() {
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    if (!width || !height) return;
    const finest = Math.max(...this.points.keys());
    for (const [level, points] of this.points) points.visible = level === finest;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
    const rect = this.canvas.getBoundingClientRect();
    for (const item of this.annotationNodes) {
      const projected = new THREE.Vector3(...item.annotation.position).project(this.camera);
      const visible = projected.z >= -1 && projected.z <= 1;
      item.node.hidden = !visible;
      if (visible) item.node.style.transform = `translate(${(projected.x+1)*rect.width/2}px, ${(1-projected.y)*rect.height/2}px) translate(-50%, -50%)`;
    }
  }
  dispose() {
    this.onChange = null;
    for (const level of [...this.points.keys()]) this.hide(level);
    this.setTrajectory([]); this.setKeyframes([]); this.annotationNodes.forEach(({node,handler}) => { node.removeEventListener("click",handler); node.remove(); });
    this.annotationNodes=[]; this.controls.removeEventListener("change",this.handleChange);
    this.canvas.removeEventListener("pointerdown",this.keyframePointerDown);
    this.canvas.removeEventListener("pointerup",this.keyframePointerUp);
    this.controls.dispose(); this.resize.disconnect(); this.renderer.dispose();
  }
}
