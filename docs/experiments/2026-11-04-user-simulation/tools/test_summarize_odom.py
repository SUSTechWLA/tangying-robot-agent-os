"""Synthetic sampling coverage tests, not robot experiment results."""
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('summarize_odom',Path(__file__).with_name('summarize_odom.py'))
audit=importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)


def fixtures():
    start=audit.timestamp('2026-11-04T00:00:01Z')
    task={'id':'synthetic','state':'SUCCEEDED','currentRevision':1,'events':[
        {'type':'TASK_APPROVED','occurredAt':'2026-11-04T00:00:01Z','payload':{'revision':1}},
        {'type':'LOCAL_RUN_FINISHED','occurredAt':'2026-11-04T00:00:03Z','payload':{'taskRevision':1}}]}
    samples=[{'event':'sample','atUnixMs':int(start+i*500),'latest':{'odom':{
        'receiptUnixMs':int(start+i*500),'sensorStampNs':(i+3)*500000000,
        'pose':[i,0,0,1,0,0,0],'linear':[1,0,0]}}} for i in range(-1,6)]
    return task,samples


def test_full_sampling_window():
    task,samples=fixtures(); result=audit.summarize(task,samples)
    assert result['samplingCoverageComplete']
    assert result['uniqueFreshOdomSamples']==5
    assert result['sampledPlanarPathM']==4
    assert result['maximumReceiptGapSeconds']==.5


def test_recording_after_task_is_unknown_not_zero():
    task,samples=fixtures(); result=audit.summarize(task,samples[-1:])
    assert not result['samplingCoverageComplete']
    assert result['sampledPlanarPathM'] is None
    assert result['uniqueFreshOdomSamples']==0
    assert result['missingPrefixSeconds']==2.5


def test_duplicate_sensor_history_does_not_add_distance():
    task,samples=fixtures()
    for sample in samples: sample['latest']=samples[2]['latest']
    result=audit.summarize(task,samples)
    assert not result['samplingCoverageComplete']
    assert result['uniqueFreshOdomSamples']==1
    assert result['sampledPlanarPathM'] is None


def test_large_gap_is_partial():
    task,samples=fixtures(); result=audit.summarize(task,[samples[0],samples[1],samples[-2],samples[-1]])
    assert not result['samplingCoverageComplete']
    assert result['maximumReceiptGapSeconds']==2
