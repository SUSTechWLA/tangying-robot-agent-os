import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
function functionSource(name) { const start = app.indexOf(`function ${name}(`); assert.ok(start >= 0); return app.slice(start, app.indexOf("\n}", start) + 2); }
function element(tag) { return { tag, children: [], dataset: {}, textContent: "", append(...values) { this.children.push(...values); }, prepend(value) { this.children.unshift(value); }, replaceChildren(...values) { this.children = values; } }; }
function fixture() { const nodes = new Map(); const context = vm.createContext({ document: { createElement: element }, $(id) { if (!nodes.has(id)) nodes.set(id, element("div")); return nodes.get(id); }, makeTextElement(tag, cls, text) { const node = element(tag); node.className = cls; node.textContent = text; return node; }, edgeURL: path => `/robots/ground-a${path}` }); return { context, nodes }; }
function words(node) { return [node.textContent, ...node.children.map(words)].join(" "); }
test("failed planning exposes only validated durable IDs and binds links to the selected robot", () => {
 const { context,nodes }=fixture();vm.runInContext(functionSource("renderPlanningFailure"),context);
 const id="planning-attempt-"+"a".repeat(32);context.renderPlanningFailure({planningAttemptId:id,message:"proposal rejected"});
 const entry=nodes.get("#planning-failure-list").children[0];
 assert.match(words(entry),new RegExp(id));assert.equal(entry.children.at(-1).href,`/robots/ground-a/v1/planning-attempts/${id}`);
 assert.equal(entry.children.at(-1).rel,"noopener");
 context.renderPlanningFailure({planningAttemptId:"../../secret"});assert.equal(nodes.get("#planning-failure-list").children.length,1);
 context.renderPlanningFailure({auditUnavailable:true});assert.match(words(nodes.get("#planning-failure-list").children[0]),/不能按零模型消耗/);
});
test("approved READY may retry startup but running or terminal tasks cannot be restarted",()=>{
 const {context,nodes}=fixture();Object.assign(context,{localActionPending:false,localRecovery:null,approveButton:element("button"),cancelButton:element("button")});vm.runInContext(functionSource("updateLocalActionButtons"),context);
 for(const state of ["READY","EXECUTING","SUCCEEDED","CANCELLED","RECOVERABLE_FAILURE"]){context.activeTask={id:"task",state,approved:true};context.updateLocalActionButtons();assert.equal(context.approveButton.disabled,state!=="READY",state);if(state==="READY") assert.match(context.approveButton.textContent,/重试启动/);}
 context.activeTask={id:"task",state:"READY",approved:true};context.localActionPending=true;context.updateLocalActionButtons();assert.equal(context.approveButton.disabled,true);
});
test("metrics label separate populations and preserve no-sample and unavailable states",()=>{
 const {context,nodes}=fixture();vm.runInContext(functionSource("percent")+"\n"+functionSource("renderMetrics"),context);
 context.renderMetrics({available:true,totalTasks:2,legacyCandidateAttempts:0,goalModelCallAttempts:3,goalProposalAttempts:2,goalAcceptedProposals:1,goalProposalAcceptanceRate:.5,goalRecipeHits:1});
 const text=words(nodes.get("#metrics-grid"));assert.match(text,/旧规划器候选通过率 无样本/);assert.match(text,/GOAL 提案通过率 50.0%/);assert.match(text,/创建前的规划失败单独归档/);assert.match(text,/不等于实际 HTTP 请求或计费 token/);
 context.renderMetrics({available:false});assert.match(words(nodes.get("#metrics-grid")),/读取失败/);assert.doesNotMatch(words(nodes.get("#metrics-grid")),/0%/);
});

test("local boot reads existing readiness after initial telemetry without starting calibration", async()=>{
 const {context}=fixture();let resolveTelemetry;const calls=[];
 Object.assign(context,{scenePageVisible:()=>true,pollTelemetry:()=>new Promise(resolve=>{resolveTelemetry=resolve;}),refreshOnboarding:async()=>{calls.push("readiness");},pollMetrics(){},pollRuntime(){},loadLLMConfig(){},loadLocalTasks(){},pollLocalWorld(){},scheduleAgentAlertPoll(){},pollLocalTask(){},setInterval(){},renderSceneFrameStats(){}});
 vm.runInContext(functionSource("startLocalMode"),context);context.startLocalMode();assert.deepEqual(calls,[]);resolveTelemetry();await Promise.resolve();await Promise.resolve();assert.deepEqual(calls,["readiness"]);
});

test("current-state metrics show cancellation separately with an all-created denominator",()=>{
 const {context,nodes}=fixture();vm.runInContext(functionSource("percent")+"\n"+functionSource("renderMetrics"),context);
 context.renderMetrics({available:true,totalTasks:8,succeededTasks:1,failedTasks:2,cancelledTasks:3,endToEndSuccessRate:.125});
 const text=words(nodes.get("#metrics-grid"));
 assert.match(text,/成功 \/ 失败 \/ 取消（当前状态） 1 \/ 2 \/ 3/);
 assert.match(text,/已创建任务成功占比（含待执行、取消） 12.5%/);
});

test("a completed activity with no purpose is not described as still executing",()=>{
 const {context,nodes}=fixture();vm.runInContext(functionSource("renderMissionActivities"),context);
 context.renderMissionActivities([{stepId:"read-1",displayName:"读取建图状态",status:"CONFIRMED",statusText:"已完成"}],[],[]);
 const text=words(nodes.get("#fleet-tool-activities"));
 assert.match(text,/已完成/);assert.match(text,/用途尚未提供/);assert.doesNotMatch(text,/正在执行/);
});
