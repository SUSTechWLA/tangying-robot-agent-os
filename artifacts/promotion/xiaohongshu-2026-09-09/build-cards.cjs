/* New SVG layouts. Source camera PNG bytes are embedded unchanged, with no crop.
 * Re-render: node build-cards.cjs
 * Requires sharp, or SHARP_MODULE pointing to an existing sharp installation.
 */
const fs = require('node:fs');
const path = require('node:path');
const sharp = require(process.env.SHARP_MODULE || 'sharp');
const out = __dirname;
const evidence = path.join(out, 'inputs');
const sources = {
  rgb: 'workcell-v2-head-color.png',
  depth: 'workcell-v2-head-depth.png',
  final: 'task-06b6fc5dfb94c65ad06ebdd6-task02-verify_place-rgb.png',
  homeRgb: 'home-scene-rgb.png',
  homeDepth: 'home-scene-depth.png',
};
const C = { ink:'#173C3B', muted:'#647B77', teal:'#087C72', light:'#E5F2ED', paper:'#FAFAF3', line:'#D7E5DF', white:'#FFFFFF', gold:'#EDC477' };
const esc = s => String(s).replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const rect = (x,y,w,h,fill=C.white,r=24,stroke='none') => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}"/>`;
const text = (x,y,s,size=30,color=C.ink,weight=400,extra='') => `<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}" ${extra}>${esc(s)}</text>`;
const line = (x1,y1,x2,y2,color=C.line,width=2) => `<path d="M${x1} ${y1}L${x2} ${y2}" fill="none" stroke="${color}" stroke-width="${width}"/>`;
const image = (key,x,y,w=456,h=342) => `<image x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,${fs.readFileSync(path.join(evidence,sources[key])).toString('base64')}"/>`;
const arrow = (x,y) => `<path d="M${x} ${y}v22m-7-7 7 7 7-7" fill="none" stroke="${C.teal}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
function shell(n,body,footer='开源项目 · 仿真验证 · 实机待接入'){
 return `<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440" viewBox="0 0 1080 1440"><style>text{font-family:'PingFang SC','Helvetica Neue','Arial',sans-serif}</style>${rect(0,0,1080,1440,C.paper,0)}${rect(72,57,12,26,C.teal,4)}${text(102,79,'躺营 / ROBOT AGENT OS',22,C.ink,600)}${text(1008,79,`${String(n).padStart(2,'0')} / 05`,22,C.muted,500,'text-anchor="end"')}${body}${line(72,1354,1008,1354)}${text(72,1396,footer,22,C.muted)}${text(1008,1396,'TANGYING',20,C.teal,600,'text-anchor="end" letter-spacing="2"')}</svg>`;
}
function cover(){
 let b=text(72,211,'让机器人',88,C.ink,650)+text(72,319,'把一句话真正做完',88,C.teal,650);
 b+=text(72,381,'躺营 · Tangying Robot Agent OS',33,C.ink,500);
 b+=text(72,433,'自然语言 → 行动 → 结果核对',30,C.muted);
 b+=image('rgb',132,478,816,612);
 b+=text(540,1130,'仿真机载相机 · 工位初始采集',24,C.muted,400,'text-anchor="middle"');
 b+=rect(72,1170,936,155,C.teal,26);
 b+=text(540,1240,'开源项目 · 求职中',54,C.white,650,'text-anchor="middle"');
 b+=text(540,1292,'限定工位仿真闭环已验证 · 实机待接入',28,'#D1ECE4',400,'text-anchor="middle"');
 return shell(1,b,'记录一次机器人 Agent 的工程实践');
}
function task(){
 let b=text(72,190,'一句话，',76,C.ink,650)+text(72,286,'拆成可验证的 18 步。',70,C.teal,650);
 b+=rect(72,336,936,138,C.white,24,C.line)+text(104,385,'用户说',24,C.muted,500)+text(104,433,'“红杯放进右盒，再把蓝瓶拿过来。”',36,C.ink,600);
 b+=text(72,539,'任务链路',25,C.muted,500)+text(1008,539,'两件物品 · 包含导航',25,C.muted,500,'text-anchor="end"');
 const steps=[['01','环境观测'],['02','目标识别'],['03','导航到位'],['04','抓取放置'],['05','再次观测'],['06','结果核对']];
 steps.forEach(([n,label],i)=>{const x=72+(i%3)*320,y=568+Math.floor(i/3)*105;b+=rect(x,y,296,82,i===5?C.teal:C.light,18)+text(x+20,y+49,n,24,i===5?'#BCE3D9':C.teal,600)+text(x+68,y+51,label,32,i===5?C.white:C.ink,600)});
 b+=image('rgb',72,820)+image('final',552,820);
 b+=text(72,1201,'工位初始采集',28,C.ink,600)+text(552,1201,'参考任务最终验证',28,C.ink,600);
 b+=text(72,1241,'仿真顶部相机 · 原始 RGB',22,C.muted)+text(552,1241,'两图来自不同采集时刻',22,C.muted);
 b+=rect(72,1271,936,54,C.light,14)+text(96,1308,'建图 / 定位模式均完成过参考任务 · 保留原始验证证据',27,C.teal,550);
 return shell(2,b);
}
function evidenceCard(){
 let b=text(72,190,'机器人看到什么，',72,C.ink,650)+text(72,286,'就留下什么证据。',72,C.teal,650);
 b+=text(72,337,'机载 RGB-D 输入 · 未观测区域保持未知',29,C.muted);
 b+=image('rgb',72,381)+image('depth',552,381);
 b+=text(72,768,'彩色图像 RGB',30,C.ink,600)+text(552,768,'深度预览 Depth',30,C.ink,600);
 b+=text(72,810,'同次仿真工位采集 · 图像完整保留，未裁切',25,C.muted);
 b+=line(72,856,1008,856);
 const blocks=[
 ['01','采集时就关联','步骤、时间与原始图像一起保存'],
 ['02','回看当时的现场','成功和失败都有对应观测可追溯'],
 ['03','恢复前先核实','结果不确定时，阻止盲目重复动作'],
 ];
 blocks.forEach(([n,title,desc],i)=>{const y=889+i*132;b+=rect(72,y+2,66,66,C.light,18)+text(105,y+46,n,28,C.teal,650,'text-anchor="middle"')+text(162,y+33,title,35,C.ink,600)+text(162,y+78,desc,28,C.muted)});
 return shell(3,b);
}
function career(){
 let b=text(72,190,'换一种机器人，',72,C.ink,650)+text(72,286,'保留上层任务逻辑。',70,C.teal,650);
 b+=text(72,337,'统一接口，让设备差异留在适配层。',29,C.muted);
 b+=rect(72,378,936,104,C.teal,22)+text(540,423,'统一任务工具 / MCP',38,C.white,650,'text-anchor="middle"')+text(540,460,'任务编排、状态查询与执行边界',25,'#D1ECE4',400,'text-anchor="middle"');
 b+=arrow(540,493);
 b+=rect(72,529,936,106,C.light,22)+text(540,576,'设备 profile · 规范三维观测',37,C.ink,600,'text-anchor="middle"')+text(540,613,'描述结构、能力、传感器来源与标定',25,C.muted,400,'text-anchor="middle"');
 b+=line(540,635,540,672,C.teal,2)+line(220,672,860,672,C.teal,2);
 const paths=[['MuJoCo','仿真已验证'],['ROS 2','导航接入路径'],['真实机器人','现场待验收']];
 paths.forEach(([title,desc],i)=>{const x=72+i*320;b+=line(x+148,672,x+148,694,C.teal,2)+rect(x,694,296,156,C.white,22,C.line)+text(x+148,756,title,34,C.ink,650,'text-anchor="middle"')+text(x+148,806,desc,28,C.teal,500,'text-anchor="middle"')});
 b+=text(540,901,'单机器人先落地 · 保留多机器人扩展接口',29,C.muted,400,'text-anchor="middle"');
 b+=rect(72,943,936,266,C.ink,28)+rect(106,977,104,37,C.gold,9)+text(158,1004,'求职中',24,C.ink,650,'text-anchor="middle"');
 b+=text(106,1081,'希望加入做机器人落地的团队',46,C.white,600)+text(106,1135,'机器人 Agent · 具身智能系统 · 机器人软件',29,'#D3E8DE')+text(106,1178,'欢迎交流项目、技术问题与工作机会。',28,'#D3E8DE');
 b+=text(72,1261,'代码与文档 / GitHub',26,C.muted,500)+text(72,1305,'github.com/SUSTechWLA/tangying-robot-agent-os',29,C.teal,550);
 return shell(4,b);
}
function homeScene(){
 let b=text(72,190,'先在家里建图，',72,C.ink,650)+text(72,286,'再谈真正的 Sim2Real。',66,C.teal,650);
 b+=text(72,337,'五个房间 · 双 RGB-D · RTAB-Map / Nav2',29,C.muted);
 b+=image('homeRgb',72,381,456,342)+image('homeDepth',552,381,456,342);
 b+=text(72,768,'底盘 RGB-D 彩色观测',29,C.ink,600)+text(552,768,'同帧深度观测',29,C.ink,600);
 b+=text(72,810,'客厅 / 走廊 / 厨房 / 卧室 / 卫生间',25,C.muted);
 b+=line(72,856,1008,856);
 b+=rect(72,890,936,112,C.light,22)+text(540,940,'自然语言路线 → 观察 → 导航 → 到达确认',35,C.ink,600,'text-anchor="middle"')+text(540,978,'未知地图区域不自动当作可通行',25,C.teal,500,'text-anchor="middle"');
 const rooms=[['客厅','起点'],['走廊','连接'],['厨房','目标'],['卧室','待探索'],['卫生间','待探索']];
 rooms.forEach(([name,desc],i)=>{const x=72+i*190;const fill=i===0?C.teal:C.white;const color=i===0?C.white:C.ink;b+=rect(x,1050,168,112,fill,20,i===0?'none':C.line)+text(x+84,1100,name,28,color,650,'text-anchor="middle"')+text(x+84,1140,desc,22,i===0?'#D1ECE4':C.teal,500,'text-anchor="middle"')});
 b+=text(72,1235,'当前探针：ready=true · 视觉词 159 / 112',26,C.teal,600)+text(72,1282,'跨房间规划遇到未知区域会安全拒绝，下一步是受控探索与实机验收。',24,C.muted);
 return shell(5,b,'家庭场景已接入 · 地图覆盖仍需现场探索');
}
async function main(){
 const cards=[['01-cover',cover()],['02-task-loop',task()],['03-evidence',evidenceCard()],['04-sim2real-career',career()],['05-home-slam',homeScene()]];
 for(const [name,svg] of cards){fs.writeFileSync(path.join(out,`${name}.svg`),svg);await sharp(Buffer.from(svg)).png().toFile(path.join(out,`${name}.png`));}
 console.log(JSON.stringify(cards.map(([name])=>({svg:path.join(out,`${name}.svg`),png:path.join(out,`${name}.png`)})),null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1});
