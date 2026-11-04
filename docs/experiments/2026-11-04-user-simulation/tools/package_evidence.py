#!/usr/bin/env python3
"""Package an explicit experiment allowlist; never inspect credentials or copy a database."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import lzma
import re
import sqlite3
import tarfile
from pathlib import Path


def sha(wire): return hashlib.sha256(wire).hexdigest()
def encode(value): return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
def check(wire,name):
    patterns=[rb'\bsk-[A-Za-z0-9_-]{24,}',rb'(?i)\bBearer[ \t]+[A-Za-z0-9._~+/-]{24,}',
              rb'(?i)"(?:api[_-]?key|authorization|password|client_secret|access_token|refresh_token|cookie|set-cookie)"\s*:\s*"[^"\s]']
    if any(re.search(pattern,wire) for pattern in patterns): raise ValueError('credential-like content in allowlisted input: '+name)
    return wire


def write(path,wire):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream: stream.write(wire)


def compressed(path,wire):
    write(path,gzip.compress(wire,compresslevel=6,mtime=0))


def archive(path,entries):
    buffer=io.BytesIO()
    compressor = lzma.LZMAFile(buffer,mode='wb',preset=6) if path.suffix == '.xz' else gzip.GzipFile(fileobj=buffer,mode='wb',compresslevel=6,mtime=0)
    with compressor as zipped:
        with tarfile.open(fileobj=zipped,mode='w') as tar:
            for name,wire in sorted(entries.items()):
                part=Path(name)
                if part.is_absolute() or '..' in part.parts: raise ValueError('unsafe archive entry')
                info=tarfile.TarInfo(name);info.size=len(wire);info.mode=0o644;info.mtime=0
                tar.addfile(info,io.BytesIO(wire))
    write(path,buffer.getvalue())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--journal',required=True,type=Path)
    args=parser.parse_args();source=args.source;out=args.output
    out.mkdir(parents=True,exist_ok=False)
    summaries=[];image_records=[]
    for number in range(1,5):
        name=f'audit-{number:03d}';directory=source/name
        manifest_wire=check((directory/'manifest.json').read_bytes(),name+'/manifest.json');manifest=json.loads(manifest_wire)
        files=manifest['files'];actual={p.name for p in directory.iterdir() if p.is_file()}
        if actual!=set(files)|{'manifest.json'}:raise ValueError('audit manifest file set differs: '+name)
        entries={name+'/manifest.json':manifest_wire}
        for filename,expected in files.items():
            if Path(filename).name!=filename or Path(filename).suffix not in ('.json','.py') or (directory/filename).is_symlink():raise ValueError('unexpected audit member')
            wire=check((directory/filename).read_bytes(),name+'/'+filename)
            if len(wire)!=expected['bytes'] or sha(wire)!=expected['sha256']:raise ValueError('audit checksum mismatch: '+name+'/'+filename)
            entries[name+'/'+filename]=wire
        code=directory/'collector-used.py' if (directory/'collector-used.py').exists() else source/'tools/collect_task_audit-001.py'
        code_wire=code.read_bytes()
        if sha(code_wire)!=manifest['collectorSHA256']:raise ValueError('collector version mismatch: '+name)
        code_target=out/'collectors'/('collector-'+manifest['collectorSHA256']+'.py')
        if not code_target.exists():write(code_target,code_wire)
        archive(out/'raw'/(name+'.tar.xz'),entries)
        for filename in ('summary.json','manifest.json'):
            write(out/name/filename,(directory/filename).read_bytes())
        summary=json.loads((directory/'summary.json').read_bytes());summaries.append(summary)
        observation=json.loads((directory/'observations.json').read_bytes())
        image_records.extend((name,record) for record in observation['records'])
    # These sources are closed observer logs, bounded evidence windows or selected
    # test results. Browser recording ZIPs, screenshots, settings, environment,
    # session storage, binaries and database files are never enumerated here.
    for number in range(1,5):
        name=f'odom-audit-{number:03d}.json'
        write(out/'odometry'/name,check((source/name).read_bytes(),name))
        if number>1:
            name=f'odom-window-{number:03d}.jsonl'
            compressed(out/'odometry'/(name+'.gz'),check((source/name).read_bytes(),name))
            name=f'odom-window-{number:03d}-source.json'
            write(out/'odometry'/name,check((source/name).read_bytes(),name))
    for number in (1,2):
        for name in (f'stationary-window-{number:03d}-summary.json',f'ros-observations-{number:03d}.stderr.log'):
            write(out/'odometry'/name,check((source/name).read_bytes(),name))
        for name in (f'stationary-window-{number:03d}.jsonl',):
            compressed(out/'odometry'/(name+'.gz'),check((source/name).read_bytes(),name))
        name=f'ros-observations-{number:03d}.jsonl'
        initial=b'\n'.join((source/name).read_bytes().splitlines()[:4])+b'\n'
        compressed(out/'odometry'/f'observer-start-{number:03d}.jsonl.gz',check(initial,name))
    write(out/'odometry/observer-continuation-002.json',(source/'observer-continuation-002.json').read_bytes())
    versions=['image-build/metadata.json','agent-cohort-002/metadata.json','agent-cohort-003/metadata.json',
              'agent-cohort-004-ui-only/metadata.json','slam-recovery/live-patch-manifest.json',
              'slam-recovery/navigation-patch-002-manifest.json','slam-recovery/gateway-patch-manifest.json',
              'slam-recovery/live-final-file-verification.json','slam-recovery/live-final-file-verification-002.json']
    for name in versions:write(out/'versions'/name,check((source/name).read_bytes(),name))
    logs=['audit-tools-pytest.log','docs-tests-final.log','go-final-tests.log','go-diagnostics-binding-tests.log',
          'go-reconciliation-tests.log','go-trace-tests-final.log','go-web-tests-final.log','web-tests-final.log',
          'trace-reconciliation-go-test.log','ui-state-tests.log']
    logs += ['slam-recovery/'+p.name for p in sorted((source/'slam-recovery').glob('navigation-patch-tests*.log'))]
    for name in logs:
        wire=check((source/name).read_bytes(),name)
        if not wire:raise ValueError('test log is not complete: '+name)
        write(out/'tests'/name,wire)
    inputs=['pre-approval-task.json','pre-approval-task-002.json','pre-approval-task-003.json','pre-approval-task-004.json',
            'preflight-telemetry.json','preflight-telemetry-002.json','pre-approval-telemetry-003.json','pre-approval-telemetry-004.json',
            'reconciliation-001.json','reconciliation-002.json','ui-trace-task-003.json','ui-trace-task-004.json',
            'navigation-command-002.json','audit-001-notes.md','audit-002-notes.md']
    inputs += ['slam-recovery/'+p.name for pattern in ('navigation-goal-results-*.json','navigation-watch-*.jsonl','navigation-watch-*-summary.json','second-failure-terminal-receipts.json')
               for p in sorted((source/'slam-recovery').glob(pattern))]
    archive(out/'raw/inputs-and-navigation.tar.xz',{name:check((source/name).read_bytes(),name) for name in inputs})
    for name in ['ui-final-reopen-check.json','ui-accessibility-result.json','post-restart-traces.json']:
        write(out/name,check((source/name).read_bytes(),name))
    metrics=[]
    for number,summary in enumerate(summaries,1):
        odom=json.loads((source/f'odom-audit-{number:03d}.json').read_bytes());usage=summary['modelUsage'];rooms=summary['patrol']['confirmations']
        metrics.append({'attempt':number,'taskId':summary['taskId'],'stateAtExecutionClosure':summary['taskState'],
                        'acceptancePassed':summary['acceptancePassed'],'recordAuditPassed':summary['recordAuditPassed'],
                        'approvalToFinishSeconds':odom['executionWindowSeconds'],'events':summary['journal']['eventCount'],
                        'steps':summary['stepCount'],'confirmedSteps':summary['stepStates'].get('CONFIRMED',0),
                        'navigationAndArrivalConfirmed':len(rooms),'requiredNavigationAndArrivalConfirmations':8,
                        'captureCount':summary['observationCount'],'capabilityRPCCount':summary['rpcCount'],
                        'odomSamplingCoverageComplete':odom['samplingCoverageComplete'],'odomSamples':odom['uniqueFreshOdomSamples'],
                        'sampledPlanarPathM':odom['sampledPlanarPathM'],'maxReceiptGapSeconds':odom['maximumReceiptGapSeconds'],
                        'maximumArrivalPositionErrorM':max((r['positionErrorM'] for r in rooms),default=None),
                        'maximumArrivalYawErrorRad':max((r['yawErrorRad'] for r in rooms),default=None),
                        'modelCalls':usage['uniqueCallIDs'],**usage['providerReportedKnownTotals']})
    write(out/'campaign-metrics.json',encode({'schemaVersion':'user.simulation.campaign.v1','attempts':metrics,
        'scope':'Sequential debugging and repeatability observations, not independent reset trials or statistical reliability evidence. Task states are frozen execution-closure states; later operator cancellations are separate history. Odometry is sampled estimate, not ground truth/contact proof. Capability RPC counts are not all middleware or Nav2 RPCs. Token counts are provider-reported, cached tokens are part of prompt tokens.'}))
    text=io.StringIO();writer=csv.DictWriter(text,fieldnames=list(metrics[0]));writer.writeheader();writer.writerows(metrics);write(out/'campaign-metrics.csv',text.getvalue().encode())
    # Explicitly authorized task-scoped export of existing images only. No new
    # capture, no whole database read/copy, and no modification of frozen audits.
    images={};index=[];total=0
    uri=args.journal.resolve(strict=True).as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True,timeout=10) as database:
        database.row_factory=sqlite3.Row;database.execute('BEGIN')
        for audit_name,record in image_records:
            row=database.execute('SELECT metadata_json,rgb,depth FROM observation_evidence WHERE task_id=? AND id=?',(record['taskId'],record['id'])).fetchone()
            if row is None:raise ValueError('task capture missing during image export')
            metadata=json.loads(row['metadata_json'])
            for key in ('id','taskId','stepId','captureId','robotId','taskRevision','snapshotSha256','rgbSha256','depthSha256'):
                if metadata.get(key)!=record.get(key):raise ValueError('capture identity changed during image export')
            item={'audit':audit_name,**{key:record.get(key) for key in ('id','taskId','taskRevision','stepId','robotId','captureId','snapshotSha256','observedAtUnixMs')},'images':{}}
            for kind in ('rgb','depth'):
                blob=row[kind]
                if not record.get(kind+'Sha256') and not blob:
                    item['images'][kind]={'status':'not_declared'};continue
                if not isinstance(blob,bytes) or sha(blob)!=record.get(kind+'Sha256'):raise ValueError('existing image checksum mismatch')
                if not re.fullmatch(r'[0-9a-f]{64}',record['id']) or not re.fullmatch(r'task-[0-9a-f]+',record['taskId']):raise ValueError('unsafe image identity')
                name=record['taskId']+'/'+record['id']+'-'+kind+'.png';images[name]=blob;total+=len(blob)
                item['images'][kind]={'status':'verified','file':name,'sha256':sha(blob),'bytes':len(blob)}
            index.append(item)
    image_report={'schemaVersion':'task.images.export.v1','captureCount':len(index),'imageCount':len(images),'imageBytes':total,
                  'bundled':total<=20*1024*1024,'records':index,'scope':'Existing task-scoped RGB/depth byte identity only; no new sensing, semantic interpretation or physical success inferred.'}
    write(out/'images-manifest.json',encode(image_report))
    if image_report['bundled']:archive(out/'images.tar.gz',images)
    excluded=['browser recording ZIP and request headers/cookies','screenshots','configuration/credential/browser session/token storage','binaries','whole databases','new sensor captures','full-period ROS raw logs (retained locally; frozen task windows and observer start evidence bundled)']
    files={str(p.relative_to(out)):{'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size} for p in sorted(out.rglob('*')) if p.is_file()}
    write(out/'PACKAGE.json',encode({'schemaVersion':'experiment.evidence.package.v1','files':files,'excluded':excluded,
       'packagerSHA256':sha(Path(__file__).read_bytes()),'auditManifestsVerified':4,'credentialPatternScan':'passed for allowlisted textual inputs; no credential source files read',
       'scope':'Frozen execution audits plus separately captured operator/odometry/version/test evidence. This package never overwrites original audits.'}))
    print(json.dumps({'output':str(out),'files':len(files)+1,'totalBytes':sum(v['bytes'] for v in files.values()),'imageBytes':total,'imageCount':len(images),'imagesBundled':image_report['bundled']}))


if __name__=='__main__':main()
