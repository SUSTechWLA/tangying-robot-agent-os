#!/usr/bin/env python3
"""Draw deterministic, offline textbook diagrams using only the standard library."""
from html import escape
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'book/assets'
INK = '#173544'
TEAL = '#006f78'
COLORS = ['#e4f1ef', '#e8edf7', '#fff0d5']


def text(x, y, value, size=18, weight='normal', anchor='start', color=INK):
    return f'<text x="{x}" y="{y}" font-family="sans-serif" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{color}">{escape(value)}</text>'


def begin(title, description, height):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 {height}" role="img" aria-labelledby="title description">',
            f'<title id="title">{escape(title)}</title><desc id="description">{escape(description)}</desc>',
            '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#006f78"/></marker></defs>',
            f'<rect width="960" height="{height}" rx="16" fill="#f4f7f7"/>',text(30,42,title,24,'bold')]


def box(x,y,title,lines,width=260,color=0):
    result=[f'<rect x="{x}" y="{y}" width="{width}" height="105" rx="10" fill="{COLORS[color]}" stroke="#9db8bf"/>',text(x+16,y+29,title,20,'bold')]
    for i,line in enumerate(lines):result.append(text(x+16,y+58+i*24,line,16))
    return result


def line(x1,y1,x2,y2,label='',label_y=None):
    result=[f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{TEAL}" stroke-width="2" marker-end="url(#arrow)"/>']
    if label:
        caption=text((x1+x2)/2,label_y if label_y is not None else (y1+y2)/2-9,label,15,anchor='middle')
        result.append(caption.replace('<text ', '<text paint-order="stroke" stroke="#f4f7f7" stroke-width="6" stroke-linejoin="round" '))
    return result


def flow(name,title,boxes,labels,note):
    """Six stations; top row left to right, bottom row right to left."""
    positions=[(30,90),(350,90),(670,90),(670,310),(350,310),(30,310)]
    result=begin(title,note,495)
    for i,(heading,first,second) in enumerate(boxes):
        result+=box(*positions[i],heading,[first,second],color=i%3)
    for i,label in enumerate(labels):
        x,y=positions[i];nx,ny=positions[i+1]
        if y==ny:
            if nx>x:result+=line(x+260,y+52,nx,y+52,label,y+10)
            else:result+=line(x,y+52,nx+260,y+52,label,y+10)
        else:result+=line(x+130,y+105,nx+130,ny,label,y+164)
    result.append(text(30,466,note,16)); result.append('</svg>')
    return name+'\n'+'\n'.join(result)+'\n'


def sequence(name,title,roles,steps,note):
    height=190+len(steps)*68
    result=begin(title,note,height)
    xs=[120+240*i for i in range(len(roles))]
    for x,role in zip(xs,roles):
        result.append(f'<rect x="{x-102}" y="75" width="204" height="44" rx="8" fill="#e4f1ef" stroke="#9db8bf"/>')
        result.append(text(x,104,role,18,'bold','middle'))
        result.append(f'<line x1="{x}" y1="119" x2="{x}" y2="{height-60}" stroke="#a8bdc2" stroke-dasharray="5 5"/>')
    for i,(start,end,label) in enumerate(steps):
        y=160+i*68
        if start == end:
            x=xs[start]
            result.append(f'<path d="M{x},{y} h60 v24 h-60" fill="none" stroke="{TEAL}" stroke-width="2" marker-end="url(#arrow)"/>')
            result.append(text(x,y-12,f'{i+1}. {label}',15,anchor='end'))
        else:
            result+=line(xs[start],y,xs[end],y,f'{i+1}. {label}',y-12)
    result.append(text(30,height-22,note,16));result.append('</svg>')
    return name+'\n'+'\n'.join(result)+'\n'


def architecture():
    r=begin('图 2-1　谁决定目标，谁拥有动作，谁证明完成','虚线表示观察与反馈；实线表示有权限的调用。',650)
    layers=[(30,95,'云端协调','粗目标、批准、claim','不进入电机控制循环',1),(350,95,'Local / Worker 入口','二选一装配同一 Host','持有本机任务生命周期',0),(670,95,'模型决策服务','读取有界上下文','输出候选子计划',1),
            (350,285,'执行与设备 Harness','合同 / 目录 / 预算 / 身份','Executor → Robot Runtime',2),
            (30,475,'证据与持久状态','Task / SQLite / journal','历史与待核对结果',1),(350,475,'ROS 2 与本体控制器','Action / 反馈 / 受控停止','AGV、足式、无人机',0),(670,475,'现场观测与验证','采集时间 / 坐标 / 来源','确认到达或保持未知',2)]
    for x,y,a,b,c,col in layers:r+=box(x,y,a,[b,c],color=col)
    r+=line(290,147,350,147,'合同',134);r+=line(610,147,670,147,'规划',134)
    r+=line(480,200,480,285,'已准入子计划',250);r+=line(480,390,480,475,'设备命令',437)
    r+=line(610,527,670,527,'测量',513)
    r.append('<path d="M350,175 L320,175 L320,440 L160,440 L160,475" fill="none" stroke="#006f78" stroke-width="2" marker-end="url(#arrow)"/>')
    r.append('<path d="M350,338 L320,338" fill="none" stroke="#006f78" stroke-width="2"/>')
    r.append(text(170,430,'留存状态与执行记录',16))
    r.append('<path d="M800,200 L800,225 L600,225 L600,200" fill="none" stroke="#006f78" stroke-width="2" stroke-dasharray="6 5" marker-end="url(#arrow)"/>')
    r.append(text(670,217,'候选计划',15))
    r.append('<path d="M800,475 L800,255 L565,255 L565,200" fill="none" stroke="#006f78" stroke-width="2" stroke-dasharray="6 5" marker-end="url(#arrow)"/>')
    r.append(text(805,355,'反馈',17));r.append(text(30,625,'规划可变化；动作所有者、执行身份与观测来源必须可核对。',17));r.append('</svg>')
    return '02-architecture\n'+'\n'.join(r)+'\n'


def checkpoint():
    r=begin('图 8-1　重启读取证据，再选择分支','同一 PENDING 状态可能对应不同物理世界；没有证据就保持未知。',680)
    r+=box(350,85,'重启读取检查点',['任务 / command / 当前权限','核对持久记录和原证据'])
    for x,title,lines in [(30,'尚无接单记录',['核对当前执行权','再判断是否首次执行']), (350,'ACCEPTED / Pending',['可能已经发生物理效果','读取精确绑定的子账本']), (670,'EXECUTED',['原完成证明已经落盘','只补传报告，等原确认'])]:
        r+=box(x,290,title,lines,color=1)
    r+=line(430,190,160,290,'无接单',230)
    r+=line(480,190,480,290,'有未决',247)
    r+=line(530,190,800,290,'已验证',230)
    r+=box(140,505,'证据不足 / 权限不符',['保留 UNKNOWN / 停止','不重复原物理动作'],color=2)
    r+=box(560,505,'完整匹配且当前可恢复',['只补父账本或原报告','不再次执行原动作'],color=0)
    r+=line(430,395,270,505,'不能确认',455)
    r+=line(530,395,690,505,'完整证据',455)
    r.append(text(30,652,'状态名不是重试许可：读过哪个持久事实，决定可以做哪个动作。',16));r.append('</svg>')
    return '08-checkpoint\n'+'\n'.join(r)+'\n'


def recovery():
    r=begin('图 11-1　恢复是有条件的分支，不是统一重试','Ops 收集证据；准入层按失败语义选择允许的动作。',650)
    r+=box(350,80,'原失败与派发边界',['绑定 task / step / command','超时不能证明设备未执行'])
    for x,title,lines in [(30,'只读调用失败',['核对期限、预算、绑定','允许有界重读']), (350,'写结果未知',['冻结普通后续写','受控停止与证据调查']), (670,'已有完整成功证据',['核对当前权限与子账本','只补缺失记账或报告'])]:
        r+=box(x,300,title,lines,color=(x//320)%3)
    r+=line(430,185,160,300,'只读',240);r+=line(480,185,480,300,'可能有副作用',248);r+=line(530,185,800,300,'可验证',240)
    r+=box(350,510,'任务裁决与回溯',['继续 / 停止 / 人工核对','保存原失败和恢复原因'])
    r+=line(160,405,430,510,'保存结果',460);r+=line(480,405,480,510,'保留未知',475);r+=line(800,405,530,510,'补记结果',460)
    r.append(text(30,637,'即使选择停止，也需要观察实际速度；停止请求本身不是物理停止证明。',16));r.append('</svg>')
    return '11-recovery\n'+'\n'.join(r)+'\n'


def heterogeneity():
    r=begin('图 10-1　统一合同，三种不同的运动可行域','三个分支是本体选择，不是依次执行。',650)
    r+=box(350,80,'共同任务与空间依据',['区域 / map / transform','目标、观测与完成判据'])
    rows=[(30,'AGV',['二维 / 地形可通行区域','转弯半径、坡度与制动']), (350,'足式',['支撑面、步态与稳定性','可跨越不等于当前可执行']), (670,'UAV',['三维净空与飞行包线','飞控状态、悬停与降落'])]
    for x,title,lines in rows:r+=box(x,300,title,lines,color=(x//320)%3)
    r+=line(430,185,160,300,'Profile',245);r+=line(480,185,480,300,'Profile',245);r+=line(530,185,800,300,'Profile',245)
    r+=box(350,510,'共同结果外壳',['command / capture / frame','后置条件仍按本体具体化'])
    r+=line(160,405,430,510,'观测',460);r+=line(480,405,480,510,'观测',475);r+=line(800,405,530,510,'观测',460)
    r.append(text(30,637,'复用生命周期与身份契约；控制器、可行域与停止判据保留设备差异。',16));r.append('</svg>')
    return '10-heterogeneity\n'+'\n'.join(r)+'\n'


def context_views():
    r=begin('图 18-1　两条上下文路径，共同坚持事实与视图分离','Host 进度与 actionloop managed context 不共享同一个压缩预算。',735)
    r+=box(30,95,'完整执行账本',['Host checkpoint.Completed','批准、预算、未决状态'],width=390)
    r+=box(540,95,'阶段来源 Document',['完整源对象与 DataJSON','guard / 当前状态 / 历史'],width=390,color=1)
    r+=box(30,295,'Host 进度投影',['精确数量 + 最近 8 项','8000 字节上限，超限拒绝'],width=390)
    r+=box(540,295,'ProjectManaged',['保护状态热留，历史冷存','视图与请求分别受预算约束'],width=390,color=1)
    r+=line(225,200,225,295,'生成受限规划输入',250)
    r+=line(735,200,735,295,'生成阶段视图与源 SHA',250)
    r+=box(30,515,'Host 候选子计划',['模型只看有界进度','完整账本仍决定准入'],width=390,color=2)
    r+=box(540,515,'actionloop 模型决策',['可按受限 scope 回查冷页','归档实际请求和工具轨迹'],width=390,color=2)
    r+=line(225,400,225,515,'规划请求',458);r+=line(735,400,735,515,'阶段请求 / 冷页读取',458)
    r.append(text(30,674,'两条路径都不能用模型摘要覆盖权威事实；必要状态放不下时，停止扩大任务。',16))
    r.append(text(30,706,'冷档案可读取 ≠ 模型实际读取；哈希绑定字节 ≠ 证明规划正确。',16));r.append('</svg>')
    return '18-context\n'+'\n'.join(r)+'\n'


def all_figures():
    yield architecture()
    yield checkpoint()
    yield recovery()
    yield heterogeneity()
    yield context_views()
    flows=[
('01-question','图 1-1　从用户一句话到可判断的任务',[('业务目标','巡检 B、C 区后返回 A','需要路线与停止边界'),('执行合同','允许什么、何时过期','资源、次数、预算'),('现场计划','依据当前能力和地图','可行路线与观测点'),('设备执行','明确命令身份','控制器完成局部闭环'),('独立验证','动作之后的新采集','比较目标与实际状态'),('任务结论','完成 / 失败 / 未知','保留证据和解释')],['形式化','细化','派发','采集','裁决'],'读图问题：哪一个节点能够证明机器人确实到达？'),
('04-world','图 4-1　传感器数据怎样变成规划依据',[('原始采集','点云 / RGB-D / 位姿','时间戳和来源'),('几何登记','frame 与 transform','保存变换版本'),('地图表达','占据 / 自由 / 未知','map 与 revision'),('语义与覆盖','目标、区域、已观察点','不能把预测当实测'),('能力约束规划','2D / 地形 / 3D','净空、支撑和未知区'),('命令后再观察','验证到达与地图一致','旧地图不产生新证据')],['配准','融合','解释','查询','复验'],'语义回答“是什么”，几何与控制约束共同回答“能否去”。'),
('05-harness','图 5-1　一个工具调用经过哪些检查',[('模型输出','工具名与参数','只是候选动作'),('目录与授权','schema / effects / resource','批准包络是否允许'),('执行上下文','robot / revision / command','当前 claim 与 fence'),('Runtime 准入','profile / ready / deadline','journal 与去重'),('控制器与反馈','执行、取消、停止','结果可能仍然未知'),('后置条件验证','新观测与目标比较','保存结论和引用')],['解析','绑定','调用','执行','证明'],'复用的是检查链；不同机器人的运动学和制动仍由本体实现。'),
('06-roles','图 6-1　角色合作的最小闭环',[('Task 角色','提出下一步计划','无权修改旧执行事实'),('Executor','校验并执行获批动作','记录失败与命令身份'),('Ops 角色','调查症状和证据','输出 diagnosis / finding'),('Recovery 角色','提出有界恢复建议','保留原失败绑定'),('确定性准入','只读或已确认无效果？','未知写进入对账'),('原任务继续或停止','只在原授权下继续','不把建议当执行成功')],['候选','事件','诊断','提案','裁决'],'多角色是责任隔离；当前 Ops / Recovery 不等于三个独立大模型。'),
('07-planning','图 7-1　目标如何进入持久化子计划',[('批准目标','robot.objective.v1','目录、grant、预算'),('持久预留','先占计划和调用额度','重启不重置预算'),('读取进度','完整账本 → 有界视图','保留完成前缀和未知'),('生成与验证计划','参数域、效果、重复次数','越权候选被拒绝'),('逐步执行','先写 pending 再调用','验证结果后记账'),('检查目标完成','独立完成读或下一代','耗尽预算则终止')],['准入','投影','规划','执行','复核'],'新计划可以改变未完成后缀，不能擦除旧动作或扩大授权。'),
('09-workspaces','图 9-1　工作台切换设备，不迁移执行器',[('浏览器工作台','当前 URL 指定设备','安全确认按页面重建'),('入口 Local Agent','只查管理员注册表','root 会话与同源检查'),('目标身份预检','核对 robotId','正确后读取私有会话'),('目标 Agent','独立 Host 与 SQLite','固定 Runtime 身份'),('任务与观测历史','相同 taskId 仍隔离','Runtime 断开可读取'),('回到浏览器','设备作用域路径','Agent 离线不回退')],['选择','预检','代理','查询','呈现'],'A 的页面关闭不会停止 A 的任务；B 不继承 A 的上下文。'),
('12-experiment','图 12-1　从研究假设到可解释结果',[('机制假设','旧响应会导致误动作？','先给可证伪预测'),('控制变量','固定任务、地图和 seed','只改变待测机制'),('故障注入','派发前 / 后 / 回执后','注明真实与模拟边界'),('测量与原始记录','分母、成功、停止、未知','保留失败和干预'),('配对分析','同场景比较差值','安全与完成率分别看'),('复现与外推','源码、配置、证据哈希','解释未覆盖条件')],['设计','注入','采集','比较','审查'],'一次成功案例可以证明路径可走通，不能估计任意场景可靠性。'),
('15-research','图 15-1　扩展点怎样形成研究问题',[('系统瓶颈','例如上下文变长','先测量而非先加模型'),('可替换策略','投影 / 规划 / 恢复','稳定合同保持不变'),('机制开关','对照、增强、消融','禁止关闭底层安全'),('共同输入','相同场景与故障时点','记录所有版本'),('多维输出','任务完成、代价、误动作','停止与 unknown 单列'),('选择与再验证','寻找约束下的折中','保留失败外推条件')],['定位','抽象','比较','测量','决策'],'最优系统必须先给评价目标、约束与适用分布。')]
    for args in flows:yield flow(*args)
    yield sequence('03-execution','图 3-1　命令返回与物理完成是两件事',['Agent','Runtime','控制器','观测 / 验证'],[(0,1,'Execute：commandId 与目标'),(1,2,'派发运动'),(2,1,'控制器返回结果'),(1,0,'工具结果：成功或不确定'),(0,3,'请求命令后的新观测'),(3,0,'身份、时效、后置条件通过')],'只有最后一步满足任务判据，才有足够依据记录物理完成。')
    yield sequence('17-cloud-edge','图 17-1　云边控制合同与完成补传',['云端协调','Worker / Host','执行库','Runtime'],[(0,1,'下行目标与 claim / Basis'),(1,2,'持久接单与预算'),(1,3,'当前权限下执行并验证'),(3,1,'原命令结果与观测'),(1,2,'保存完成证明与 Outbox'),(1,0,'上报原完成证明'),(0,1,'原回执确认，丢失后可重发')],'断连后的原结果可以补传；旧 claim 不能被解释为新的运动授权。')
    yield sequence('17-peer','图 17-2　数据已送达，离业务采纳还有两层',['发送 Agent','发送 Outbox','接收 Inbox','接收端消费者'],[(0,1,'持久提交 observation 信封'),(1,2,'身份、sequence、TTL、Basis'),(2,1,'ACK：已接受这封消息'),(3,2,'按持久 feed 游标读取'),(2,3,'原 payload 与来源'),(3,3,'生成本地建议；不授予动作')],'ACK ≠ 采用覆盖建议 ≠ 目标物理完成。不同地图依据先拒绝合并。')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    expected={}
    for item in all_figures():
        name,svg=item.split('\n',1);expected[name+'.svg']=svg
    if args.check:
        for name,svg in expected.items():
            if not (OUT/name).exists() or (OUT/name).read_text()!=svg:raise SystemExit('Stale figure: '+name)
    else:
        for name,svg in expected.items():(OUT/name).write_text(svg)
    print(f'{len(expected)} textbook figures '+('verified' if args.check else 'generated'))

if __name__=='__main__':main()
