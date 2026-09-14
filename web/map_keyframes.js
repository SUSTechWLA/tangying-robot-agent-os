// Immutable SLAM evidence inspection. Never queries live robot observations.
(function (global) {
  "use strict";
  const MAX_FRAMES=400, MAX_SESSION_BYTES=2000000, MAX_PREVIEW_BYTES=12*1024*1024;
  // Registration attempts per keyframe: one adjacent fit, plus every revisit
  // candidate tried from up to two headings. The budget used to be two per
  // frame, which refused every real survey - measured 2.1 to 3.5 per frame on
  // published maps, and nine in the worst case - so the panel reported a format
  // error instead of showing keyframes at all.
  const MAX_ATTEMPTS_PER_FRAME=9;
  // What a registration attempt may be called. The backend writes its refusal
  // reason (`no_overlap`, `underconstrained`, ...) into `status` and reserves
  // `accepted` for a fit that passed every gate. Anything shaped like a reason
  // is kept and shown verbatim: a new reason added to the SLAM must never be
  // able to hide the whole panel again, and only `accepted` counts as evidence.
  const ATTEMPT_STATUS=/^[a-z][a-z_]{2,31}$/;
  const REASON_LABELS=Object.freeze({
    accepted:"已接受",
    weak_loop:"回环未通过门限",
    too_few_points:"可用点太少",
    no_surface_model:"参考面没有有效法向",
    no_overlap:"与参考子图重叠不足",
    too_flat_or_few_normals:"有效法向太少（表面过于平坦）",
    singular_system:"方程奇异",
    nonfinite_update:"求解结果非有限",
    no_inliers:"没有内点",
    too_flat_at_final:"收敛后仍过于平坦",
    low_overlap:"内点率过低",
    large_residual:"残差过大",
    underconstrained:"观测方向不足",
    correction_too_large:"修正量超出本步所观测到的运动",
  });
  const reasonLabel=status=>REASON_LABELS[status]||status;
  const finitePose=value=>Array.isArray(value)&&value.length===3&&value.every(Number.isFinite);
  const identity=map=>`${map?.mapId || ""}\n${map?.hash || ""}`;
  const digest=async bytes=>Array.from(new Uint8Array(await global.crypto.subtle.digest("SHA-256",bytes)),n=>n.toString(16).padStart(2,"0")).join("");
  function sameMap(raw,map) {
    if(!raw || raw.mapId!==map.mapId || raw.calibrationRevision!==map.calibrationRevision
      || (raw.robotId!==undefined && raw.robotId!==map.robotId) || (raw.frameId!==undefined && raw.frameId!=="map")) throw new Error("关键帧证据与所选地图不一致");
  }
  function normalizeSession(raw,map) {
    sameMap(raw,map);
    if(raw.schemaVersion!=="slam.session.v1" || !Array.isArray(raw.observations) || raw.observations.length>MAX_FRAMES) throw new Error("关键帧元数据格式或数量无效");
    const ids=new Set();
    const frames=raw.observations.map((entry,index)=>{
      const id=entry.frameId || `kf-${String(index).padStart(4,"0")}`;
      if(typeof id!=="string" || id.length>128 || ids.has(id) || typeof entry.id!=="string" || !entry.id || entry.id.length>512
        || !Number.isSafeInteger(entry.stamp) || entry.stamp<=0 || !Number.isFinite(new Date(entry.stamp).getTime()) || (entry.index!==undefined && entry.index!==index) || !finitePose(entry.odometry) || !finitePose(entry.optimizedPose)
        || (entry.baseZ!==undefined && !Number.isFinite(entry.baseZ))
        || (entry.pointCount!==undefined && (!Number.isInteger(entry.pointCount)||entry.pointCount<0))) throw new Error("关键帧身份、时间或位姿无效");
      ids.add(id);
      const yaw=Math.atan2(Math.sin(entry.optimizedPose[2]-entry.odometry[2]),Math.cos(entry.optimizedPose[2]-entry.odometry[2]));
      return {...entry, frameId:id, index, position:[entry.optimizedPose[0],entry.optimizedPose[1],entry.baseZ ?? 0],
        correction:{translationM:Math.hypot(entry.optimizedPose[0]-entry.odometry[0],entry.optimizedPose[1]-entry.odometry[1]),yawRad:yaw}};
    });
    const attempts=raw.registrationAttempts || [...(raw.registrations||[]).map(row=>({...row,kind:"adjacent",status:"accepted"})),...(raw.loopClosures||[]).map(row=>({...row,kind:"loop",status:"accepted"}))];
    if(!Array.isArray(attempts)||attempts.length>MAX_FRAMES*MAX_ATTEMPTS_PER_FRAME) throw new Error("配准记录超出预算");
    const links=[];
    for(const link of attempts) {
      if(!Number.isInteger(link.from)||!Number.isInteger(link.to)||link.from<0||link.to<=link.from||link.to>=frames.length
        || !["adjacent","loop"].includes(link.kind)||typeof link.status!=="string"||!ATTEMPT_STATUS.test(link.status)
        || (link.rmseM!==undefined && (!Number.isFinite(link.rmseM)||link.rmseM<0))
        || (link.inlierRatio!==undefined && (!Number.isFinite(link.inlierRatio)||link.inlierRatio<0||link.inlierRatio>1))) throw new Error("配准记录无效");
      links.push({...link, accepted:link.status==="accepted"});
    }
    for(const frame of frames) {
      frame.links=links.filter(row=>row.from===frame.index||row.to===frame.index);
      frame.hasLoop=frame.links.some(row=>row.kind==="loop"&&row.accepted);
      // Whether this frame entered the map on its own measurement, or only on
      // odometry because every fit against it was refused. An operator looking
      // at a drifted stretch needs that distinction per frame, not per map.
      frame.registered=frame.links.some(row=>row.kind==="adjacent"&&row.accepted);
    }
    const accepted=links.filter(row=>row.accepted).length;
    const reasons=new Map();
    for(const row of links) if(!row.accepted) reasons.set(row.status,(reasons.get(row.status)||0)+1);
    return {frames, name:typeof raw.name==="string"?raw.name.slice(0,120):"",algorithm:String(raw.algorithm||"未保存"),
      assumptions:raw.assumptions, keyframeSelection:raw.keyframeSelection, mapIdentity:identity(map),
      quality:{attempts:links.length, accepted, refused:links.length-accepted,
        reasons:[...reasons].sort((left,right)=>right[1]-left[1])}};
  }
  function normalizePreviews(raw,map,session) {
    sameMap(raw,map);
    if(raw.schemaVersion!=="slam.keyframes.v1" || raw.frameId!=="map" || raw.encoding?.kind!=="capture_previews"
      || raw.encoding.depth!=="nearest-sample-fixed-scale-preview" || raw.encoding.rgb!=="jpeg-quality-78"
      || raw.encoding.rawDepthSaved!==false || !Array.isArray(raw.encoding.depthRangeM) || raw.encoding.depthRangeM.length!==2
      || raw.encoding.depthRangeM[0]!==.02 || raw.encoding.depthRangeM[1]!==5 || raw.encoding.depthColors!=="near-warm-far-cool" || raw.encoding.invalidDepth!=="black"
      || !Array.isArray(raw.frames)||raw.frames.length>MAX_FRAMES) throw new Error("关键帧图像包格式无效");
    const frames=new Map(), expected=new Map(session.frames.map(row=>[row.frameId,row]));
    let bytes=0;
    for(const entry of raw.frames) {
      const match=expected.get(entry.frameId);
      if(!match||frames.has(entry.frameId)||match.id!==entry.observationId||match.stamp!==entry.stamp||!["saved","budget_exhausted"].includes(entry.status)) throw new Error("关键帧图像身份不匹配");
      if(entry.status==="saved") for(const kind of ["rgb","depth"]) {
        const image=entry[kind];
        if(!image || image.mediaType!==(kind==="rgb"?"image/jpeg":"image/png") || !Number.isInteger(image.bytes)||image.bytes<1||image.bytes>128*1024
          || !Number.isInteger(image.width)||image.width<1||image.width>240||!Number.isInteger(image.height)||image.height<1||image.height>180
          || !/^[a-f0-9]{64}$/.test(image.sha256)||typeof image.data!=="string"||image.data.length!==4*Math.ceil(image.bytes/3)||!/^[A-Za-z0-9+/]*={0,2}$/.test(image.data)) throw new Error("关键帧图像超出预算或格式无效");
        bytes+=image.bytes;
      }
      if(entry.status==="saved" && (entry.rgb.bytes+entry.depth.bytes>128*1024 || entry.rgb.width!==entry.depth.width||entry.rgb.height!==entry.depth.height)) throw new Error("RGB 与深度预览尺寸不匹配");
      frames.set(entry.frameId,entry);
    }
    if(bytes>8*1024*1024) throw new Error("关键帧图像总量超出预算");
    return {frames,encoding:raw.encoding};
  }
  async function fetchArtifact(map,role,{fetcher=global.fetch,signal}={}) {
    const entry=map.artifacts?.[role], limit=role==="slam_session"?MAX_SESSION_BYTES:MAX_PREVIEW_BYTES;
    if(!entry) return null;
    if(!Number.isSafeInteger(entry.bytes)||entry.bytes<1||entry.bytes>limit||!/^[a-f0-9]{64}$/.test(entry.sha256)) throw new Error("关键帧文件超出预算或缺少内容校验");
    const response=await fetcher(`/v1/maps/${encodeURIComponent(map.mapId)}/artifact/${role}?sha256=${entry.sha256}`,{cache:"no-store",signal});
    if(!response.ok) throw new Error(`关键帧文件读取失败 (${response.status})`);
    const chunks=[]; let count=0;
    const reader=response.body.getReader();
    try {
      while(true) {
        const {value,done}=await reader.read(); if(done) break;
        count+=value.length; if(count>entry.bytes) throw new Error("关键帧文件大小与清单不一致"); chunks.push(value);
      }
    } catch(error) { await reader.cancel(); throw error; }
    if(count!==entry.bytes) throw new Error("关键帧文件不完整");
    const bytes=new Uint8Array(count); let offset=0;
    for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    if(await digest(bytes)!==entry.sha256) throw new Error("关键帧文件内容校验失败");
    return JSON.parse(new TextDecoder().decode(bytes));
  }
  function imageDimensions(bytes,mime) {
    if(mime==="image/png") {
      if(bytes.length<33 || ![137,80,78,71,13,10,26,10].every((value,index)=>bytes[index]===value)) return null;
      const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
      return [view.getUint32(16),view.getUint32(20)];
    }
    if(bytes[0]!==255||bytes[1]!==216) return null;
    let i=2;
    while(i+8<bytes.length) {
      if(bytes[i]!==255) return null;
      const marker=bytes[i+1], size=bytes[i+2]*256+bytes[i+3];
      if(size<2||i+size+2>bytes.length) return null;
      if([192,193,194].includes(marker)) return [bytes[i+7]*256+bytes[i+8],bytes[i+5]*256+bytes[i+6]];
      i+=size+2;
    }
    return null;
  }
  async function imageBlob(entry) {
    const bytes=Uint8Array.from(global.atob(entry.data),char=>char.charCodeAt(0));
    const size=imageDimensions(bytes,entry.mediaType);
    if(bytes.length!==entry.bytes || !size || size[0]!==entry.width||size[1]!==entry.height || await digest(bytes)!==entry.sha256) throw new Error("关键帧图像内容或尺寸校验失败");
    return new Blob([bytes],{type:entry.mediaType});
  }
  const number=(value,digits=3)=>Number.isFinite(value)?value.toFixed(digits):"未保存";
  const pose=value=>finitePose(value)?`x ${number(value[0])} · y ${number(value[1])} m · θ ${number(value[2])} rad`:"未保存";
  const element=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
  class Inspector {
    constructor({list,summary,toggle,dialog,onName}) {
      Object.assign(this,{list,summary,toggle,dialog,onName}); this.generation=0; this.openGeneration=0;
      toggle?.addEventListener("change",()=>this.viewer?.showKeyframes(toggle.checked));
      dialog?.querySelector("[data-keyframe-close]")?.addEventListener("click",()=>dialog.close());
      dialog?.addEventListener("close",()=>{this.openGeneration++;this.viewer?.selectKeyframe(null);this.releaseImages();});
      dialog?.querySelector("[data-keyframe-prev]")?.addEventListener("click",()=>this.open(this.selectedIndex-1));
      dialog?.querySelector("[data-keyframe-next]")?.addEventListener("click",()=>this.open(this.selectedIndex+1));
      dialog?.querySelector("[data-keyframe-focus]")?.addEventListener("click",()=>{const frame=this.session?.frames[this.selectedIndex];dialog.close();if(frame)this.viewer?.focus(frame.position);});
      // Arrow keys walk the survey without closing the dialog: an operator
      // comparing consecutive frames of a drifted stretch should not have to
      // aim at two small buttons between every capture.
      dialog?.addEventListener("keydown",event=>{
        if(event.defaultPrevented||event.altKey||event.ctrlKey||event.metaKey)return;
        const step=event.key==="ArrowLeft"?-1:event.key==="ArrowRight"?1:0;
        if(!step||this.selectedIndex===undefined)return;
        if(!this.session?.frames[this.selectedIndex+step])return;
        event.preventDefault();this.open(this.selectedIndex+step);
      });
    }
    releaseImages() {
      for(const url of this.imageURLs||[])URL.revokeObjectURL(url);this.imageURLs=[];
      this.dialog?.querySelector("[data-keyframe-images]")?.replaceChildren();
    }
    reset() {
      this.releaseImages();
      this.generation++; this.openGeneration++; this.abort?.abort(); this.dialog?.close();
      this.viewer?.setKeyframes([]); this.viewer=null; this.map=null;this.session=null;this.previewPromise=null;
      if(this.list)this.list.replaceChildren(); if(this.summary)this.summary.textContent="选择地图后读取关键帧。";
      if(this.dialog) {this.dialog.querySelector("[data-keyframe-images]")?.replaceChildren();this.dialog.querySelector("[data-keyframe-fields]")?.replaceChildren();}
    }
    async load(map,viewer) {
      this.reset(); this.map=map;this.viewer=viewer;const generation=this.generation;this.abort=new AbortController();
      this.summary.textContent="正在校验关键帧…";
      try {
        const raw=await fetchArtifact(map,"slam_session",{signal:this.abort.signal});
        if(generation!==this.generation)return;
        if(!raw){this.summary.textContent="此地图未保存 SLAM 关键帧信息。";return;}
        this.session=normalizeSession(raw,map);this.onName?.(this.session.name,map);
        const frames=this.session.frames, quality=this.session.quality;
        const loops=new Set(frames.flatMap(row=>row.links.filter(link=>link.kind==="loop"&&link.accepted).map(link=>`${link.from}:${link.to}`)));
        const top=quality.reasons[0];
        const refusals=quality.refused?` · 未通过 ${quality.refused} 次（主要：${reasonLabel(top[0])}）`:"";
        this.summary.textContent=`${frames.length} 个关键帧 · 配准通过 ${quality.accepted}/${quality.attempts}${refusals} · ${loops.size} 个已接受回环。点击地图中的方向标记或下方帧列表分析。`;
        this.renderList(0);viewer?.setKeyframes(frames,frame=>this.open(frame.index));viewer?.showKeyframes(this.toggle.checked);
      } catch(error) {if(generation===this.generation)this.summary.textContent=String(error.message||error);}
    }
    renderList(page) {
      this.page=page;this.list.replaceChildren();const frames=this.session?.frames||[];
      for(const frame of frames.slice(page*12,page*12+12)) {
        const button=element("button",undefined,"saved-keyframe-row");button.type="button";
        const marks=[frame.hasLoop?"回环":null,frame.registered?null:"仅里程计"].filter(Boolean);
        button.append(element("strong",`#${String(frame.index+1).padStart(3,"0")}${marks.length?" · "+marks.join(" · "):""}`),
          element("small",`${new Date(frame.stamp).toLocaleTimeString()} · 修正 ${number(frame.correction.translationM)} m`));
        button.addEventListener("click",()=>this.open(frame.index));this.list.append(button);
      }
      if(frames.length>12) {
        const pager=element("div",undefined,"saved-keyframe-pager");
        for(const [label,next] of [["上一页",page-1],["下一页",page+1]]) {const b=element("button",label,"secondary");b.type="button";b.disabled=next<0||next*12>=frames.length;b.addEventListener("click",()=>this.renderList(next));pager.append(b);}
        pager.append(element("small",`${page+1} / ${Math.ceil(frames.length/12)}`));this.list.append(pager);
      }
    }
    async open(index) {
      const frame=this.session?.frames[index];if(!frame||!this.dialog)return;
      const generation=this.generation, token=++this.openGeneration, map=this.map, session=this.session;this.selectedIndex=index;this.releaseImages();
      const valid=()=>generation===this.generation&&token===this.openGeneration&&identity(this.map)===this.session?.mapIdentity&&this.dialog.open;
      this.viewer?.selectKeyframe(frame.frameId);
      this.dialog.querySelector("[data-keyframe-title]").textContent=`关键帧 #${String(index+1).padStart(3,"0")}`;
      this.dialog.querySelector("[data-keyframe-identity]").textContent=`${this.session.name||this.map.mapId} · ${frame.frameId} · 只读采集证据`;
      const previous=this.dialog.querySelector("[data-keyframe-prev]"),next=this.dialog.querySelector("[data-keyframe-next]");previous.disabled=index===0;next.disabled=index===this.session.frames.length-1;
      const fields=this.dialog.querySelector("[data-keyframe-fields]");fields.replaceChildren();
      const ratio=Number.isFinite(frame.validDepthPixels)&&frame.width*frame.height?`${(frame.validDepthPixels/(frame.width*frame.height)*100).toFixed(1)}%`:"未保存";
      for(const [label,value] of [["采集时间",new Date(frame.stamp).toISOString()+` (${frame.stamp})`],["观测 ID",frame.id],["传感器来源",frame.sourceId||"未保存"],["相机坐标系",frame.cameraFrameId||"未保存"],
        ["原始里程计 · x/y/θ",pose(frame.odometry)],["优化地图位姿 · x/y/θ",pose(frame.optimizedPose)],["位姿修正",`${number(frame.correction.translationM)} m · ${number(frame.correction.yawRad)} rad`],
        ["采集点 / 有效深度",`${frame.pointCount??"未保存"} 点 · ${ratio}`],["原始尺寸 / 自身遮罩",`${frame.width&&frame.height?`${frame.width} × ${frame.height}`:"未保存"} · ${frame.selfMaskedPixels??"未保存"} 像素`],
        ["深度范围",`${number(frame.depthMinM)} – ${number(frame.depthMaxM)} m`],["算法",this.session.algorithm],["地图坐标系",`map · 高度 ${frame.baseZ===undefined?"未保存，标记投影到 z=0":number(frame.baseZ)+" m"}`]]) {
        fields.append(element("dt",label),element("dd",String(value)));
      }
      const links=this.dialog.querySelector("[data-keyframe-links]");links.replaceChildren();
      if(!frame.links.length)links.append(element("p","此帧没有保存的配准记录。","muted"));
      for(const link of frame.links)links.append(element("p",
        `#${link.from+1} → #${link.to+1} · ${link.kind==="loop"?"回环":"相邻帧"} · ${link.accepted?"已接受":`未通过：${reasonLabel(link.status)}`}`
        +` · RMSE ${number(link.rmseM)} m · 内点率 ${Number.isFinite(link.inlierRatio)?(link.inlierRatio*100).toFixed(1)+"%":"未保存"}`
        +`${Number.isFinite(link.correspondences)?` · 对应点 ${link.correspondences}`:""}`
        +`${Number.isFinite(link.correctionM)?` · 修正 ${number(link.correctionM)} m`:""}`,
        `keyframe-link ${link.accepted?"accepted":"rejected"}`));
      const raw=this.dialog.querySelector("[data-keyframe-raw]");raw.textContent=JSON.stringify({mapId:this.map.mapId,mapRevision:this.map.hash,calibrationRevision:this.map.calibrationRevision,keyframeSelection:this.session.keyframeSelection,...frame},null,2);
      const images=this.dialog.querySelector("[data-keyframe-images]");images.replaceChildren(element("p","正在校验此帧的 RGB / 深度预览…","muted"));
      if(!this.dialog.open)this.dialog.showModal();
      try {
        if(!this.map.artifacts?.slam_keyframes){images.replaceChildren(element("p","此历史地图未保存关键帧图像；仅显示该地图已有的元数据。","keyframe-missing"));return;}
        this.previewPromise ||= fetchArtifact(map,"slam_keyframes",{signal:this.abort.signal}).then(raw=>normalizePreviews(raw,map,session));
        const previews=await this.previewPromise;if(!valid())return;
        const pair=previews.frames.get(frame.frameId);
        if(!pair||pair.status!=="saved"){images.replaceChildren(element("p",pair?.status==="budget_exhausted"?"此帧图像未保存：已达到采集预览预算。位姿和配准元数据仍保留。":"此帧没有已保存图像。","keyframe-missing"));return;}
        const blobs=await Promise.all([imageBlob(pair.rgb),imageBlob(pair.depth)]);if(!valid())return;images.replaceChildren();
        const urls=blobs.map(blob=>URL.createObjectURL(blob));this.imageURLs=urls;
        for(const [i,label] of ["RGB 采集缩略图 · JPEG", "深度预览 · 0.02–5 m · 近暖远冷 · 黑色为无效/超量程"].entries()) {
          const figure=element("figure"),img=element("img");img.src=urls[i];img.alt=label;img.width=pair.rgb.width;img.height=pair.rgb.height;
          figure.append(img,element("figcaption",`${label} · ${pair.rgb.width}×${pair.rgb.height}`));images.append(figure);
        }
      } catch(error) {if(valid())images.replaceChildren(element("p",String(error.message||error),"keyframe-missing"));}
    }
  }
  global.TangyingMapKeyframes=Object.freeze({MAX_FRAMES,MAX_SESSION_BYTES,MAX_PREVIEW_BYTES,normalizeSession,normalizePreviews,fetchArtifact,imageBlob,imageDimensions,identity,Inspector});
})(globalThis);
