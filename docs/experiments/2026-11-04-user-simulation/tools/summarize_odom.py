#!/usr/bin/env python3
"""Summarize already recorded ROS odometry; never connect to or control a robot."""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000


def summarize(task, samples):
    revision = task['currentRevision']
    approvals = [timestamp(e['occurredAt']) for e in task['events'] if e['type'] == 'TASK_APPROVED'
                 and e.get('payload',{}).get('revision') == revision]
    finishes = [timestamp(e['occurredAt']) for e in task['events'] if e['type'] == 'LOCAL_RUN_FINISHED'
                and e.get('payload',{}).get('taskRevision') == revision]
    if not approvals or not finishes:
        raise ValueError('current revision needs recorded approval and run-finished events')
    start,end = min(approvals),max(finishes)
    if end < start:
        raise ValueError('run finish predates approval')
    samples = sorted((s for s in samples if s.get('event') == 'sample'),key=lambda s:s['atUnixMs'])
    first,last = (samples[0]['atUnixMs'],samples[-1]['atUnixMs']) if samples else (None,None)
    inside = [s for s in samples if start <= s['atUnixMs'] <= end]
    poses,seen,stale,invalid = [],set(),0,0
    for sample in inside:
        odom = sample.get('latest',{}).get('odom') or {}
        pose,receipt,stamp = odom.get('pose'),odom.get('receiptUnixMs'),odom.get('sensorStampNs')
        if (not isinstance(pose,list) or len(pose) != 7
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in pose)
                or type(receipt) is not int or type(stamp) is not int):
            invalid += 1
            continue
        # Messages retained in the observer are history, not fresh sensor samples.
        if not start <= receipt <= end or not 0 <= sample['atUnixMs']-receipt <= 1000:
            stale += 1
            continue
        identity = (receipt,stamp)
        if identity in seen:
            continue
        seen.add(identity)
        poses.append({'sampleUnixMs':sample['atUnixMs'],'receiptUnixMs':receipt,'sensorStampNs':stamp,
                      'pose':pose,'linear':odom.get('linear'),'angular':odom.get('angular')})
    gaps = [(b['receiptUnixMs']-a['receiptUnixMs'])/1000 for a,b in zip(poses,poses[1:])]
    nonmonotonic = sum(b['sensorStampNs'] <= a['sensorStampNs'] for a,b in zip(poses,poses[1:]))
    path = sum(math.hypot(b['pose'][0]-a['pose'][0],b['pose'][1]-a['pose'][1]) for a,b in zip(poses,poses[1:])) if len(poses)>1 else None
    speeds = [math.sqrt(sum(v*v for v in p['linear'])) for p in poses
              if isinstance(p['linear'],list) and len(p['linear']) == 3
              and all(type(v) in (int,float) and math.isfinite(v) for v in p['linear'])]
    prefix = max(0,first-start)/1000 if first is not None else None
    suffix = max(0,end-last)/1000 if last is not None else None
    coverage = (first is not None and first <= start and last >= end and len(poses)>1
                and (poses[0]['receiptUnixMs']-start) <= 1000
                and (end-poses[-1]['receiptUnixMs']) <= 1000
                and max(gaps,default=math.inf) <= 1 and not nonmonotonic)
    return {'schemaVersion':'sampled.odometry.audit.v1','taskId':task['id'],'taskRevision':revision,
            'taskState':task['state'],'approvalUnixMs':start,'runFinishedUnixMs':end,'executionWindowSeconds':(end-start)/1000,
            'sourceFirstSampleUnixMs':first,'sourceLastSampleUnixMs':last,'sourceSampleCount':len(samples),
            'samplesInsideWindow':len(inside),'uniqueFreshOdomSamples':len(poses),
            'staleOrOutOfWindowOdomExcluded':stale,'invalidOrMissingOdomSamples':invalid,
            'missingPrefixSeconds':prefix,'missingSuffixSeconds':suffix,'samplingCoverageComplete':coverage,
            'firstUsedReceiptUnixMs':poses[0]['receiptUnixMs'] if poses else None,
            'lastUsedReceiptUnixMs':poses[-1]['receiptUnixMs'] if poses else None,
            'maximumReceiptGapSeconds':max(gaps) if gaps else None,'nonmonotonicSensorStampCount':nonmonotonic,
            'sampledPlanarPathM':path,'maximumReportedLinearSpeedMPS':max(speeds) if speeds else None,
            'sensorElapsedSeconds':(poses[-1]['sensorStampNs']-poses[0]['sensorStampNs'])/1e9 if len(poses)>1 else None,
            'scope':'Approximate 2 Hz odometry polyline during approval-to-finish, not total continuous trajectory or independent ground truth. Missing coverage is unknown, never zero. No contact/collision proof. Complete means recording brackets the interval, used first/last messages within 1 s of its bounds, maximum message gap <=1 s and sensor stamps monotonic.',
            'sampledPoses':poses}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',required=True,type=Path)
    parser.add_argument('--samples',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    task_wire,sample_wire = args.task.read_bytes(),args.samples.read_bytes()
    result = summarize(json.loads(task_wire),[json.loads(line) for line in sample_wire.splitlines() if line.strip()])
    result['sources'] = {str(path):{'sha256':hashlib.sha256(wire).hexdigest(),'bytes':len(wire)}
                         for path,wire in [(args.task,task_wire),(args.samples,sample_wire)]}
    with args.output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('sampledPoses','scope','sources')},ensure_ascii=False))


if __name__ == '__main__': main()
