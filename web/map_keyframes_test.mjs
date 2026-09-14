import test from "node:test";
import assert from "node:assert/strict";
import {createHash,webcrypto} from "node:crypto";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const context=vm.createContext({crypto:webcrypto,Blob,URL,Uint8Array,DataView,TextDecoder,AbortController,Set,Map,console,atob,fetch});
vm.runInContext(readFileSync(new URL("./map_keyframes.js",import.meta.url),"utf8"),context);
const K=context.TangyingMapKeyframes;
const hash=bytes=>createHash("sha256").update(bytes).digest("hex");
const map=(id="home")=>({mapId:id,hash:id==="home"?"a".repeat(64):"b".repeat(64),robotId:"unit",calibrationRevision:"c".repeat(64),frameId:"map",artifacts:{}});
const session=(m=map())=>({schemaVersion:"slam.session.v1",mapId:m.mapId,robotId:m.robotId,calibrationRevision:m.calibrationRevision,name:"家庭地图",observations:[
  {id:"obs-0",stamp:1000,odometry:[0,0,0],optimizedPose:[.1,0,0]},
  {id:"obs-1",stamp:2000,odometry:[.3,0,0],optimizedPose:[.4,.1,.03]},
],registrations:[{from:0,to:1,rmseM:.02,inlierRatio:.8}],loopClosures:[]});
const metadata=(raw)=>{const bytes=Buffer.from(JSON.stringify(raw));return{bytes:bytes.length,sha256:hash(bytes),href:"evidence.json"};};

test("legacy frames preserve only stored evidence, with deterministic IDs and accepted link quality",()=>{
  const result=K.normalizeSession(session(),map());
  assert.equal(result.frames[0].frameId,"kf-0000");
  assert.equal(result.frames[1].links[0].status,"accepted");
  assert.equal(result.frames[0].sourceId,undefined);
  assert.equal(result.frames[0].pointCount,undefined);
  assert.equal(result.frames[1].correction.translationM,Math.hypot(.4-.3,.1));
  assert.equal(result.name,"家庭地图");
});

test("session rejects wrong maps, duplicate IDs, malformed poses, oversized frames and invalid links",()=>{
  assert.throws(()=>K.normalizeSession(session(map("other")),map()),/不一致/);
  for(const change of [raw=>raw.observations[0].optimizedPose[0]=Infinity, raw=>raw.observations.forEach(row=>row.frameId="duplicate"),
    raw=>raw.observations=Array(401).fill(raw.observations[0]),raw=>raw.registrations[0].to=2,raw=>raw.registrations[0].inlierRatio=1.1,raw=>raw.observations[0].stamp=9000000000000000]) {
    const raw=session();change(raw);assert.throws(()=>K.normalizeSession(raw,map()));
  }
});

test("preview identities must match both observation and capture timestamp of selected map",()=>{
  const m=map(), normalized=K.normalizeSession(session(),m);
  const raw={schemaVersion:"slam.keyframes.v1",mapId:m.mapId,robotId:m.robotId,calibrationRevision:m.calibrationRevision,frameId:"map",encoding:{kind:"capture_previews",depth:"nearest-sample-fixed-scale-preview",rgb:"jpeg-quality-78",rawDepthSaved:false,depthRangeM:[.02,5],depthColors:"near-warm-far-cool",invalidDepth:"black"},frames:[{frameId:"kf-0000",observationId:"obs-0",stamp:1000,status:"budget_exhausted"}]};
  assert.equal(K.normalizePreviews(raw,m,normalized).frames.get("kf-0000").status,"budget_exhausted");
  assert.throws(()=>K.normalizePreviews({...raw,encoding:{...raw.encoding,depthRangeM:[.02,8]}},m,normalized),/格式/);
  assert.throws(()=>K.normalizePreviews({...raw,frames:[{...raw.frames[0],stamp:1001}]},m,normalized),/不匹配/);
  assert.throws(()=>K.normalizePreviews({...raw,frames:[{...raw.frames[0],observationId:"other"}]},m,normalized),/不匹配/);
});

test("artifact transport enforces declared size, content SHA and pre-download budget",async()=>{
  const m=map(), raw=session();m.artifacts.slam_session=metadata(raw);
  let requested="";
  const actual=await K.fetchArtifact(m,"slam_session",{fetcher:async url=>{requested=url;return new Response(JSON.stringify(raw));}});
  assert.equal(actual.mapId,m.mapId);assert.match(requested,new RegExp(m.artifacts.slam_session.sha256));
  await assert.rejects(K.fetchArtifact(m,"slam_session",{fetcher:async()=>new Response("x".repeat(m.artifacts.slam_session.bytes))}),/校验/);
  await assert.rejects(K.fetchArtifact(m,"slam_session",{fetcher:async()=>new Response("x".repeat(m.artifacts.slam_session.bytes+1))}),/大小/);
  m.artifacts.slam_session.bytes=2000001;
  await assert.rejects(K.fetchArtifact(m,"slam_session",{fetcher:async()=>{assert.fail("oversized request must not fetch");}}),/预算/);
});

class Node {
  constructor(tag="div") {this.tag=tag;this.children=[];this.listeners={};this.nodes={};this.open=false;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(name,callback){this.listeners[name]=callback;}
  querySelector(selector){return this.nodes[selector] ||= new Node();}
  showModal(){this.open=true;}
  close(){if(this.open){this.open=false;this.listeners.close?.();}}
}
context.document={createElement:tag=>new Node(tag)};
const defer=()=>{let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};};
function inspector() {
  const result=new K.Inspector({list:new Node(),summary:new Node(),toggle:Object.assign(new Node(),{checked:true}),dialog:new Node("dialog")});
  return result;
}

test("delayed metadata from previous map cannot replace current markers or selected name",async()=>{
  const a=map(),b=map("other"),rawA=session(a),rawB=session(b),delayed=defer();
  a.artifacts.slam_session=metadata(rawA);b.artifacts.slam_session=metadata(rawB);
  context.fetch=async url=>url.includes("/home/")?delayed.promise:new Response(JSON.stringify(rawB));
  const viewer={setKeyframes(){},showKeyframes(){},selectKeyframe(){}}, inspect=inspector();
  const old=inspect.load(a,viewer);await inspect.load(b,viewer);
  delayed.resolve(new Response(JSON.stringify(rawA)));await old;
  assert.equal(inspect.session.mapIdentity,K.identity(b));assert.equal(inspect.map.mapId,"other");
});

test("old map dialog explicitly reports unrecorded images, and switching maps closes it",async()=>{
  const m=map(),raw=session(m);m.artifacts.slam_session=metadata(raw);
  context.fetch=async()=>new Response(JSON.stringify(raw));
  const inspect=inspector();await inspect.load(m,{setKeyframes(){},showKeyframes(){},selectKeyframe(){}});await inspect.open(0);
  assert.equal(inspect.dialog.open,true);
  assert.match(inspect.dialog.querySelector("[data-keyframe-images]").children[0].textContent,/历史地图未保存/);
  inspect.reset();assert.equal(inspect.dialog.open,false);assert.equal(inspect.session,null);
});

test("late preview response cannot reopen or fill a dialog after map reset",async()=>{
  const m=map(),raw=session(m),delayed=defer();m.artifacts.slam_session=metadata(raw);
  const preview={schemaVersion:"slam.keyframes.v1",mapId:m.mapId,robotId:m.robotId,calibrationRevision:m.calibrationRevision,frameId:"map",encoding:{kind:"capture_previews",depth:"nearest-sample-fixed-scale-preview",rgb:"jpeg-quality-78",rawDepthSaved:false,depthRangeM:[.02,5],depthColors:"near-warm-far-cool",invalidDepth:"black"},frames:[{frameId:"kf-0000",observationId:"obs-0",stamp:1000,status:"budget_exhausted"}]};
  m.artifacts.slam_keyframes=metadata(preview);
  context.fetch=async url=>url.includes("slam_keyframes")?delayed.promise:new Response(JSON.stringify(raw));
  const inspect=inspector();await inspect.load(m,{setKeyframes(){},showKeyframes(){},selectKeyframe(){}});const opening=inspect.open(0);
  inspect.reset();delayed.resolve(new Response(JSON.stringify(preview)));await opening;
  assert.equal(inspect.dialog.open,false);assert.equal(inspect.dialog.querySelector("[data-keyframe-images]").children.length,0);
});

test("inspector releases every Blob URL when changing or closing a frame",()=>{
  const inspect=inspector(), revoked=[];
  const old=context.URL;context.URL={revokeObjectURL:url=>revoked.push(url)};
  inspect.imageURLs=["blob:first","blob:second"];inspect.releaseImages();
  assert.deepEqual(revoked,["blob:first","blob:second"]);assert.equal(inspect.imageURLs.length,0);context.URL=old;
});

const JPEG_DATA="/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDHooor5o+wP//Z";
const PNG_DATA="iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAAD0lEQVR4nGMUkTvBwMAAAAQ8APwDvaxLAAAAAElFTkSuQmCC";
function imageEntry(data,mime) {const bytes=Buffer.from(data,"base64");return{data,mediaType:mime,bytes:bytes.length,sha256:hash(bytes),width:2,height:1};}
test("a real survey's refusal vocabulary and attempt count still open the panel",()=>{
  // Every published map carries one attempt per adjacent pair plus up to eight
  // loop guesses, and a refused attempt records *why* (`no_overlap`,
  // `underconstrained`, `correction_too_large`, ...). The panel used to accept
  // only "accepted"/"rejected" within two attempts per frame, so it refused
  // every real map and showed a format error where the keyframes belong.
  const m=map(),raw=session(m);
  raw.registrationAttempts=[];
  const reasons=["no_overlap","too_flat_or_few_normals","weak_loop","correction_too_large","underconstrained","accepted"];
  for(let to=1;to<raw.observations.length;to+=1)
    for(let index=0;index<4;index+=1) raw.registrationAttempts.push({from:0,to,kind:index?"loop":"adjacent",
      status:reasons[(index+to*2)%reasons.length],rmseM:.02,inlierRatio:.7});
  const result=K.normalizeSession(raw,m);
  assert.equal(result.frames.length,2);
  assert.equal(result.quality.attempts,4,"four attempts for the one adjacent pair");
  // Every attempt lands in both endpoint frames; the map summary counts it once.
  assert.equal(result.frames[0].links.length,4);
  assert.equal(result.frames[0].links.filter(l=>l.accepted).length,result.quality.accepted);
  assert.equal(result.quality.refused,4-result.quality.accepted);
  assert.equal(result.quality.refused,3,"three of the four sampled attempts were refused");
  assert.equal(result.quality.accepted,1,"one attempt passed every gate");
  assert.ok(result.quality.reasons.length>0);
  assert.ok(result.quality.reasons.every(([,count])=>count>0));
  // Only `accepted` is evidence: a refusal never marks a frame registered.
  for(const frame of result.frames) for(const link of frame.links)
    assert.equal(link.accepted,link.status==="accepted");
});

test("an unknown refusal reason is shown, never fatal",()=>{
  const m=map(),raw=session(m);
  raw.registrationAttempts=[{from:0,to:1,kind:"adjacent",status:"some_future_reason",rmseM:.02,inlierRatio:.5}];
  const result=K.normalizeSession(raw,m);
  assert.equal(result.frames[1].registered,false);
  assert.deepEqual([...result.quality.reasons].map(([reason,count])=>[String(reason),Number(count)]),[["some_future_reason",1]]);
  // But a status that is not even reason-shaped, or an oversized record set, is
  // still refused: the panel fails closed on a malformed document.
  const broken=session(m);
  broken.registrationAttempts=[{from:0,to:1,kind:"adjacent",status:"Not A Reason!",rmseM:.02}];
  assert.throws(()=>K.normalizeSession(broken,m),/配准记录无效/);
  const oversized=session(m);
  oversized.registrationAttempts=Array(400*9+1).fill({from:0,to:1,kind:"adjacent",status:"accepted"});
  assert.throws(()=>K.normalizeSession(oversized,m),/超出预算/);
});

test("a frame that only odometry carried is marked as such",()=>{
  const m=map(),raw=session(m);
  raw.registrationAttempts=[{from:0,to:1,kind:"adjacent",status:"no_overlap"},{from:0,to:1,kind:"loop",status:"accepted"}];
  const result=K.normalizeSession(raw,m);
  assert.equal(result.frames[1].registered,false,"the adjacent fit was refused");
  assert.equal(result.frames[1].hasLoop,true,"the accepted loop is still a loop");
});

test("image decode validates JPEG/PNG pixels and returns only CSP-compatible blobs",async()=>{
  for(const entry of [imageEntry(JPEG_DATA,"image/jpeg"),imageEntry(PNG_DATA,"image/png")]) {
    const blob=await K.imageBlob(entry);assert.equal(blob.type,entry.mediaType);assert.equal(blob.size,entry.bytes);
    await assert.rejects(K.imageBlob({...entry,width:240}),/尺寸/);
    await assert.rejects(K.imageBlob({...entry,sha256:"f".repeat(64)}),/校验/);
  }
});

test("switching frames while previews load displays the chosen captured pair, then releases blobs",async()=>{
  const m=map(),raw=session(m),delayed=defer();m.artifacts.slam_session=metadata(raw);
  const preview={schemaVersion:"slam.keyframes.v1",mapId:m.mapId,robotId:m.robotId,calibrationRevision:m.calibrationRevision,frameId:"map",encoding:{kind:"capture_previews",depth:"nearest-sample-fixed-scale-preview",rgb:"jpeg-quality-78",rawDepthSaved:false,depthRangeM:[.02,5],depthColors:"near-warm-far-cool",invalidDepth:"black"},frames:raw.observations.map((row,index)=>({frameId:`kf-${String(index).padStart(4,"0")}`,observationId:row.id,stamp:row.stamp,status:"saved",rgb:imageEntry(JPEG_DATA,"image/jpeg"),depth:imageEntry(PNG_DATA,"image/png")}))};
  m.artifacts.slam_keyframes=metadata(preview);context.fetch=async url=>url.includes("slam_keyframes")?delayed.promise:new Response(JSON.stringify(raw));
  const inspect=inspector();await inspect.load(m,{setKeyframes(){},showKeyframes(){},selectKeyframe(){}});
  const old=context.URL,created=[],revoked=[];
  context.URL={createObjectURL:blob=>{const url=`blob:test-${created.length}`;created.push({url,blob});return url;},revokeObjectURL:url=>revoked.push(url)};
  try {
    const first=inspect.open(0),second=inspect.open(1);delayed.resolve(new Response(JSON.stringify(preview)));await Promise.all([first,second]);
    assert.equal(inspect.selectedIndex,1);assert.equal(created.length,2);assert.match(inspect.dialog.querySelector("[data-keyframe-title]").textContent,/#002/);
    assert.equal(inspect.dialog.querySelector("[data-keyframe-images]").children[0].children[0].src,"blob:test-0");
    inspect.dialog.close();assert.equal(revoked.length,2);assert.equal(inspect.dialog.querySelector("[data-keyframe-images]").children.length,0);
  } finally {context.URL=old;}
});


test("3D keyframe picking uses displayed map positions and respects hidden layer",async()=>{
  const {MapViewer}=await import("./src/map_viewer.js");
  const THREE=await import("three");
  const viewer=Object.create(MapViewer.prototype);
  viewer.canvas={getBoundingClientRect:()=>({left:20,top:30,width:600,height:400})};
  viewer.camera=new THREE.PerspectiveCamera(48,1.5,.02,100);
  viewer.camera.up.set(0,0,1);viewer.camera.position.set(0,-5,3);viewer.camera.lookAt(0,0,0);viewer.camera.updateMatrixWorld();
  viewer.keyframesVisible=true;viewer.keyframes=[{frameId:"first",position:[0,0,0]},{frameId:"far",position:[4,0,0]}];
  const projected=new THREE.Vector3(0,0,.06).project(viewer.camera);
  const x=20+(projected.x+1)*300,y=30+(1-projected.y)*200;
  assert.equal(viewer.pickKeyframe(x,y).frameId,"first");
  assert.equal(viewer.pickKeyframe(-100,-100),null);
  viewer.keyframesVisible=false;assert.equal(viewer.pickKeyframe(x,y),null);
});
