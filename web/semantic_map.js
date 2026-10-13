// Version-bound, operator-authored point-cloud semantics. Service mutations use
// the same authenticated Robot Runtime entry point as calibration and mapping.
(() => {
  "use strict";
  const copy = value => JSON.parse(JSON.stringify(value));
  const vector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);

  function bounds(value) {
    if (!vector(value?.min) || !vector(value?.max)) throw new Error("边界必须包含六个有限的 map 坐标，单位为米。");
    if (value.min.some((number, axis) => number >= value.max[axis])) throw new Error("每个轴的最小值必须小于最大值；平面选区请补足厚度。");
    return { min: [...value.min], max: [...value.max] };
  }

  function boundsFromPoints(first, second) {
    if (!vector(first) || !vector(second)) throw new Error("请在实测点云中选择两个点。");
    return { min: first.map((number, axis) => Math.min(number, second[axis])), max: first.map((number, axis) => Math.max(number, second[axis])) };
  }

  function matchesMap(snapshot, map) {
    const active = snapshot?.activeMap;
    return Boolean(map && active?.mapId === map.mapId && active?.mapRevision
      && active.mapRevision === (map.hash || map.mapRevision || map.revision)
      && active.calibrationRevision === map.calibrationRevision && snapshot.frameId === "map");
  }

  function acceptSnapshot(snapshot, map) {
    if (!matchesMap(snapshot, map)) throw new Error("所选地图不是当前激活的同一地图版本。请在上方选择或激活当前地图后重新读取。");
    if (typeof snapshot.semanticRevision !== "string" || !/^[a-f0-9]{64}$/.test(snapshot.semanticRevision) || !Array.isArray(snapshot.annotations)
      || snapshot.annotationCount !== snapshot.annotations.length || snapshot.truncated === true) {
      throw new Error("服务未返回完整语义快照，禁止整批保存。请重新读取。");
    }
    return copy(snapshot);
  }

  function annotationInput(value) {
    const result = {
      id: String(value?.id || "").trim(), name: String(value?.name || "").trim(),
      category: String(value?.category || "").trim(), kind: value?.kind,
      aliases: Array.isArray(value?.aliases) ? [...value.aliases] : [],
      bounds: bounds(value?.bounds), source: copy(value?.source || {}),
      confidence: value?.confidence, observedAtUnixMs: value?.observedAtUnixMs,
    };
    if (!result.id || !result.name || !result.category) throw new Error("请填写标注名称和类别。");
    if (!["object", "region"].includes(result.kind)) throw new Error("请选择物体或区域。");
    if (result.aliases.some(alias => typeof alias !== "string" || !alias.trim())) throw new Error("别名必须是非空文字。");
    if (!result.source.kind || !String(result.source.reference || "").trim()) throw new Error("请填写标注依据，便于后续回溯。");
    if (!Number.isFinite(result.confidence) || result.confidence < 0 || result.confidence > 1
      || !Number.isSafeInteger(result.observedAtUnixMs) || result.observedAtUnixMs <= 0) throw new Error("标注置信度或观测时间无效。");
    return result;
  }

  function saveParameters(snapshot, map, candidate) {
    acceptSnapshot(snapshot, map);
    const annotation = annotationInput(candidate);
    const rows = snapshot.annotations.map(annotationInput);
    if (new Set(rows.map(row => row.id)).size !== rows.length) throw new Error("快照包含重复标注 ID，禁止覆盖。");
    const index = rows.findIndex(row => row.id === annotation.id);
    if (index < 0) rows.push(annotation); else rows[index] = annotation;
    return { mapRevision: snapshot.activeMap.mapRevision, expectedSemanticRevision: snapshot.semanticRevision, annotations: rows };
  }

  function queryParameters(filters, snapshot) {
    const result = { limit: 50, offset: 0, includeStale: true, mapRevision: snapshot.activeMap.mapRevision, semanticRevision: snapshot.semanticRevision };
    for (const key of ["name", "category", "kind"]) if (String(filters?.[key] || "").trim()) result[key] = String(filters[key]).trim();
    return result;
  }

  class Session {
    constructor(client) { this.client = client; this.generation = 0; this.map = null; this.snapshot = null; this.busy = false; this.conflicted = false; }
    select(map) { this.generation += 1; this.map = map ? copy(map) : null; this.snapshot = null; this.busy = false; this.conflicted = false; }
    async read() {
      if (!this.map) throw new Error("请先选择已保存地图。");
      const generation = ++this.generation;
      this.snapshot = null; this.busy = true;
      try {
        const result = await this.client.call("semantic.map", {});
        if (generation !== this.generation) return null;
        this.snapshot = acceptSnapshot(result, this.map); this.conflicted = false;
        return this.snapshot;
      } finally { if (generation === this.generation) this.busy = false; }
    }
    async save(candidate) {
      if (this.busy || this.conflicted || !this.snapshot) throw new Error("请先重新读取完整语义层并核对版本后保存。");
      const parameters = saveParameters(this.snapshot, this.map, candidate), generation = this.generation;
      this.busy = true;
      try {
        let result = await this.client.call("semantic.annotate", parameters);
        if (generation !== this.generation) return null;
        if (result?.committed === true && result?.activeMapChanged === true) {
          // The write belongs to the old map and is already durable. Do not
          // report a rejection, retry it, or reuse its snapshot on another map.
          this.snapshot = null; this.conflicted = true;
          return result;
        }
        if (!Array.isArray(result?.annotations)) result = await this.client.call("semantic.map", {});
        if (generation !== this.generation) return null;
        this.snapshot = acceptSnapshot(result, this.map);
        return this.snapshot;
      } catch (error) {
        if (generation === this.generation) {
          // A failed or uncertain write must not be replayed from an old batch.
          this.conflicted = true;
        }
        throw error;
      } finally { if (generation === this.generation) this.busy = false; }
    }
    async query(filters) {
      if (this.busy || this.conflicted || !this.snapshot) throw new Error("请先重新读取语义层。");
      const generation = this.generation, revision = this.snapshot.semanticRevision;
      const result = await this.client.call("semantic.query", queryParameters(filters, this.snapshot));
      if (generation !== this.generation || revision !== this.snapshot?.semanticRevision) return null;
      if (result.semanticRevision !== revision || !matchesMap(result, this.map) || !Array.isArray(result.results)) throw new Error("查询返回了不同语义版本，请重新读取。");
      return result;
    }
  }

  class Panel {
    constructor(root, client = globalThis.TangyingRobotServices.createServiceClient()) {
      this.root = root; this.session = new Session(client); this.viewer = null; this.picking = false; this.firstPoint = null;
      this.$ = selector => root.querySelector(selector);
      this.$("[data-semantic-refresh]").addEventListener("click", () => void this.read());
      this.$("[data-semantic-new]").addEventListener("click", () => this.clearForm());
      this.$("[data-semantic-pick]").addEventListener("click", () => this.setPicking(!this.picking));
      this.$("[data-semantic-form]").addEventListener("submit", event => { event.preventDefault(); void this.save(); });
      this.$("[data-semantic-form]").addEventListener("input", () => this.preview());
      this.$("[data-semantic-query]").addEventListener("submit", event => { event.preventDefault(); void this.query(); });
      this.canvasHandler = event => this.pick(event);
      this.clearForm(); this.render();
    }
    message(text, tone = "neutral") { const node = this.$("[data-semantic-feedback]"); node.textContent = text; node.dataset.tone = ({ error: "danger", success: "good" })[tone] || tone; node.hidden = !text; }
    setMap(map, viewer) {
      this.setPicking(false);
      this.viewer?.canvas.removeEventListener("dblclick", this.canvasHandler, true);
      this.viewer?.setSemanticBounds?.([]);
      this.viewer = viewer; this.session.select(map);
      this.viewer?.canvas.addEventListener("dblclick", this.canvasHandler, true);
      this.clearForm(); this.render();
      if (map) void this.read(); else this.message("选择已保存地图后读取当前激活地图的语义层。");
    }
    setPicking(enabled) {
      const next = Boolean(enabled && this.viewer && this.session.snapshot && !this.session.busy && !this.session.conflicted);
      if (next && !this.picking) {
        this.onPickStart?.();
        this.previousKeyframePick = this.viewer.keyframePickEnabled;
        this.viewer.keyframePickEnabled = false;
        this.viewer.canvas.scrollIntoView?.({ block: "center", behavior: "instant" });
      } else if (!next && this.picking && this.viewer) this.viewer.keyframePickEnabled = this.previousKeyframePick;
      this.picking = next;
      this.firstPoint = null;
      this.$("[data-semantic-pick]").setAttribute("aria-pressed", String(this.picking));
      if (this.picking) this.message("在上方点云中依次双击两个实测点，形成包围盒；完成后核对并调整六个坐标。");
    }
    pick(event) {
      if (!this.picking) return;
      event.stopImmediatePropagation(); event.preventDefault();
      const picked = this.viewer?.pick(event.clientX, event.clientY);
      if (!vector(picked?.position)) return this.message("此处没有可选实测点，请放大点云后重选。", "warning");
      if (!this.firstPoint) { this.firstPoint = [...picked.position]; this.message("第一个点已选中。请双击第二个点，形成三维包围盒。"); return; }
      this.fillBounds(boundsFromPoints(this.firstPoint, picked.position)); this.setPicking(false); this.preview();
      this.message("选区已填入。请核对高度和厚度；点数与质心将在服务器校验保存后计算。");
    }
    field(name) { return this.$(`[name="${name}"]`); }
    fillBounds(value) { for (const side of ["min", "max"]) for (let axis = 0; axis < 3; axis += 1) this.field(`${side}${axis}`).value = String(value[side][axis]); }
    formBounds() {
      const result = {};
      for (const side of ["min", "max"]) result[side] = [0, 1, 2].map(axis => { const value = this.field(`${side}${axis}`).value.trim(); return value ? Number(value) : NaN; });
      return bounds(result);
    }
    setObservedTime(unixMs) {
      this.field("observedAt").value = Number.isFinite(unixMs) && unixMs > 0
        ? new Date(unixMs - new Date(unixMs).getTimezoneOffset() * 60000).toISOString().slice(0, 23) : "";
    }
    clearForm() {
      this.setPicking(false); this.$("[data-semantic-form]").reset(); this.editingId = "";
      this.setObservedTime(this.session.snapshot?.mapCreatedAtUnixMs || this.session.map?.createdAtUnixMs);
      this.$("[data-semantic-form-title]").textContent = "新建标注";
      this.viewer?.setSemanticBounds?.(this.session.snapshot?.annotations || []);
    }
    edit(annotation) {
      // Filter results are a bounded summary. The complete frozen overview owns
      // aliases, provenance and observation time for a subsequent whole-batch edit.
      const original = this.session.snapshot?.annotations.find(row => row.id === annotation?.id);
      if (!original) { this.message("标注不在已读取的完整版本中，请重新读取后再编辑。", "error"); return; }
      annotation = original;
      this.clearForm(); this.editingId = annotation.id;
      for (const key of ["name", "category", "kind"]) this.field(key).value = annotation[key] || "";
      this.field("aliases").value = (annotation.aliases || []).join("，");
      this.field("reference").value = annotation.source?.reference || "";
      this.setObservedTime(annotation.observedAtUnixMs);
      this.fillBounds(annotation.bounds);
      this.$("[data-semantic-form-title]").textContent = `编辑：${annotation.name}`;
      this.viewer?.focus(annotation.centroid || annotation.bounds.min.map((n, axis) => (n + annotation.bounds.max[axis]) / 2)); this.preview();
    }
    preview() {
      try {
        const selection = this.formBounds();
        this.viewer?.setSemanticBounds?.(this.session.snapshot?.annotations || [], selection);
      } catch (_) { this.viewer?.setSemanticBounds?.(this.session.snapshot?.annotations || []); }
    }
    async read() {
      this.setPicking(false); this.message("正在读取版本绑定的语义层…");
      const promise = this.session.read(), generation = this.session.generation; this.render();
      try {
        const result = await promise;
        if (!result) return;
        this.clearForm(); this.render(); this.message("已读取完整语义层。人工标注只更新地图语义，保存不会发出运动命令。");
      } catch (error) { if (generation === this.session.generation) this.message(String(error.message || error), "error"); }
      finally { if (generation === this.session.generation) this.render(); }
    }
    async save() {
      let candidate;
      try {
        const original = this.editingId
          ? this.session.snapshot?.annotations.find(row => row.id === this.editingId) : null;
        if (this.editingId && !original) throw new Error("原始标注已不可用，请重新读取后再编辑。");
        candidate = annotationInput({ id: this.editingId || globalThis.TangyingRobotServices.requestId("annotation"),
          name: this.field("name").value, category: this.field("category").value, kind: this.field("kind").value,
          aliases: this.field("aliases").value.split(/[,，]/).map(value => value.trim()).filter(Boolean),
          bounds: this.formBounds(), source: { kind: original ? original.source.kind : "operator", reference: this.field("reference").value.trim() },
          confidence: original ? original.confidence : 1, observedAtUnixMs: new Date(this.field("observedAt").value).getTime() });
      } catch (error) { return this.message(error.message, "error"); }
      this.setPicking(false); const generation = this.session.generation;
      const promise = this.session.save(candidate); this.render(); this.message("正在校验点云选区并保存语义版本…");
      try {
        const result = await promise;
        if (!result) return;
        this.clearForm(); this.render();
        if (result.committed === true && result.activeMapChanged === true) {
          this.message("标注已保存到提交时的原地图，但当前激活地图已变化。请在上方选择当前地图并重新读取；本次已保存的标注不会重试或迁移到新图。", "warning");
        } else this.message("语义标注已保存。点数和质心来自服务器对实测点云的计算。", "success");
      } catch (error) {
        if (generation === this.session.generation) this.message(`${error.message || error} 请点击“重新读取”并核对最新标注；本次提交不会自动重试。`, "error");
      } finally { if (generation === this.session.generation) this.render(); }
    }
    async query() {
      const generation = this.session.generation;
      try {
        const result = await this.session.query({ name: this.$("[data-semantic-search]").value });
        if (result) { this.renderList(result.results); this.message(`筛选显示 ${result.results.length} / ${result.total ?? result.results.length} 条（最多 50 条，包含历史过期标注）；保存仍基于已读取的完整版本。`); }
      } catch (error) { if (generation === this.session.generation) this.message(`${error.message || error} 如版本变化，请重新读取。`, "error"); }
    }
    renderList(rows) {
      const list = this.$("[data-semantic-list]"); list.replaceChildren();
      if (!rows?.length) { const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "当前没有符合条件的人工点云标注。"; list.append(empty); return; }
      for (const row of rows) {
        const item = document.createElement("article"); item.className = "semantic-map-item";
        const heading = document.createElement("button"); heading.type = "button"; heading.className = "secondary";
        heading.textContent = `${row.name} · ${row.kind === "region" ? "区域" : "物体"} · ${row.category}`;
        heading.disabled = this.session.busy || this.session.conflicted;
        heading.addEventListener("click", () => this.edit(row));
        const detail = document.createElement("p");
        const centroid = vector(row.centroid) ? row.centroid.map(n => n.toFixed(3)).join(", ") : "未提供";
        const observed = Number.isFinite(row.observedAtUnixMs) ? new Date(row.observedAtUnixMs).toLocaleString() : "未知";
        const freshness = row.freshness === "clock_disagreement" ? "观测时钟不一致，需复核" : row.stale === true ? "已过期，需重新观察" : row.stale === false ? "未超出有效期，执行前仍需重新观察" : "未提供有效期判定";
        const conflicts = (this.session.snapshot?.conflicts || []).filter(conflict => conflict.annotationIds?.includes(row.id))
          .map(conflict => `${conflict.annotationIds.filter(id => id !== row.id).join("、")}（共享 ${conflict.sharedPointCount ?? "未知"} 点，需复核）`).join("；");
        detail.textContent = `${row.pointCount ?? "未知"} 个实测点 · 质心 (${centroid}) m · 观测 ${observed} · 时效 ${freshness}${row.ambiguous ? " · 存在歧义" : ""}${conflicts ? ` · 冲突：${conflicts}` : ""}`;
        const source = document.createElement("p"); source.className = "muted"; source.textContent = `依据：${row.source?.kind || "未知"} / ${row.source?.reference || "未提供"}`;
        const measured = document.createElement("p"); measured.className = "muted";
        measured.textContent = row.measuredBounds ? `实测包围盒：${JSON.stringify(row.measuredBounds)}` : "实测包围盒：未提供";
        item.append(heading, detail, source, measured); list.append(item);
      }
    }
    render() {
      const { snapshot, busy, conflicted, map } = this.session;
      const ready = Boolean(snapshot && !busy && !conflicted);
      this.$("[data-semantic-fields]").disabled = !ready;
      this.$("[data-semantic-refresh]").disabled = busy || !map;
      this.$("[data-semantic-query-submit]").disabled = !ready;
      this.$("[data-semantic-pick]").disabled = !ready || !this.viewer;
      const status = this.$("[data-semantic-summary]");
      const coverage = Number.isFinite(snapshot?.coverage?.fraction) ? ` · 实测点覆盖 ${(snapshot.coverage.fraction * 100).toFixed(1)}%` : "";
      status.textContent = snapshot ? `${snapshot.annotationCount} 条标注${coverage} · ${snapshot.conflictCount ?? snapshot.conflicts?.length ?? 0} 处冲突 · 坐标 map · ${snapshot.activeMap.mapId}${conflicted ? " · 需要重读" : ""}` : "只有当前激活地图的同一版本可以编辑语义。";
      this.$("[data-semantic-version]").textContent = snapshot ? `地图 ${snapshot.activeMap.mapRevision}\n语义 ${snapshot.semanticRevision}\n标定 ${snapshot.activeMap.calibrationRevision}\n点云 ${snapshot.cloudSha256 || "未知"}\n覆盖 ${JSON.stringify(snapshot.coverage || {})}` : "尚未读取版本";
      this.renderList(snapshot?.annotations || []);
      this.preview();
    }
  }

  globalThis.TangyingSemanticMap = Object.freeze({ bounds, boundsFromPoints, matchesMap, acceptSnapshot, annotationInput, saveParameters, queryParameters, Session, Panel });
})();
