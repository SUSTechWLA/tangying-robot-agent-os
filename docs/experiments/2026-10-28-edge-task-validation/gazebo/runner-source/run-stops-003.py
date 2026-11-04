import json,os,signal,subprocess,time,urllib.request,hashlib
from pathlib import Path
ROOT=Path('/tmp/tangying-gazebo-field-20261028-001')
E=ROOT/'evidence'/'run-003-stops'; E.mkdir(exist_ok=False); S=ROOT/'stop-source'; processes=[]; logs=[]
summary={'baseline':'a98c0681d','startedAtUnixMs':int(time.time()*1000),'naturalLanguageEntry':None,'kind':'independent fresh-world physical stop trial','patch':['sim/gazebo/field_hub.py','scripts/run_gazebo_stop_checks.py'],'newGoalRecipeUsageHierarchicalHostValidated':False,'rosDomainId':128,'gzPartition':'tangying-field-20261028-001','seed':[3],'robots':['ground-a','ground-b','uav-a'],'worldResetDuringTasks':False,'commands':[]}
def save(): (E/'orchestration.json').write_text(json.dumps(summary,indent=2)+'\n')
def launch(cmd,name):
 log=(E/name).open('w');logs.append(log);summary['commands'].append(cmd);save()
 p=subprocess.Popen(cmd,cwd=S,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);processes.append(p);return p
def stop(p):
 if p.poll() is None:
  p.send_signal(signal.SIGINT)
  try:p.wait(timeout=18)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
try:
 subprocess.run(['/opt/navigation-venv/bin/python','sim/gazebo/build_field_world.py','--output',str(E/'world.sdf')],cwd=S,check=True)
 summary['worldSha256']=hashlib.sha256((E/'world.sdf').read_bytes()).hexdigest();save()
 gz=launch(['gz','sim','-s','--headless-rendering',str(E/'world.sdf')],'gazebo.log')
 time.sleep(4)
 if gz.poll() is not None:raise RuntimeError('Gazebo exited before hub startup')
 hub=launch(['/opt/navigation-venv/bin/python','sim/gazebo/field_hub.py','--transport',str(ROOT/'build/field-transport'),'--output',str(E/'hub'),'--port','18128'],'hub.log')
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 ready=False
 for _ in range(80):
  if hub.poll() is not None:raise RuntimeError('field hub exited before readiness')
  try:
   with opener.open('http://127.0.0.1:18128/snapshot',timeout=2) as r:data=json.load(r)
   if set(data.get('states',{})) == {'ground-a','ground-b','uav-a'}:
    (E/'initial-snapshot.json').write_text(json.dumps(data,indent=2)+'\n');ready=True;break
  except (OSError,ValueError):pass
  time.sleep(.25)
 if not ready:raise RuntimeError('field hub has no measured robot snapshot')
 stops=launch(['/opt/navigation-venv/bin/python','scripts/run_gazebo_stop_checks.py','--output',str(E/'stops-001'),'--hub','http://127.0.0.1:18128','--robots','ground-a','ground-b','uav-a'],'stops-001.log')
 try:summary['stopsExitCode']=stops.wait(timeout=240)
 except subprocess.TimeoutExpired:stop(stops);summary['stopsExitCode']=124
 summary['success']=summary.get('stopsExitCode')==0
except Exception as exc:summary['error']=str(exc);summary['success']=False
finally:
 for p in reversed(processes):stop(p)
 for log in logs:log.close()
 summary['endedAtUnixMs']=int(time.time()*1000);save()
print(json.dumps(summary,indent=2))
