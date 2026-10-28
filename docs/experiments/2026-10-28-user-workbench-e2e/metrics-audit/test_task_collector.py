import copy
import importlib.util
import json
from pathlib import Path

import pytest

BASE=Path(__file__).resolve().parent
SCRIPT=BASE/'collect_task_closure.py'
spec=importlib.util.spec_from_file_location('collector',SCRIPT);collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)


def task(state='SUCCEEDED'):
    events=[{'sequence':1,'type':'state.transition','payload':{'state':state}}]
    if state=='SUCCEEDED':events.append({'sequence':2,'type':'LOCAL_RUN_SUCCEEDED','payload':{'taskRevision':1}})
    events.append({'sequence':3,'type':'LOCAL_RUN_FINISHED','payload':{'state':state,'taskRevision':1}})
    return {'id':'task-fixture','state':state,'currentRevision':1,'events':events}


@pytest.mark.parametrize('state',['SUCCEEDED','RECOVERABLE_FAILURE','FAILED'])
def test_explicit_terminal_closure(state):
    assert collector.closure_status(task(state))['verified']


@pytest.mark.parametrize('mutation',['missing','wrong_revision','wrong_state','nonterminal','stale_finish','missing_success'])
def test_stable_poll_cannot_replace_current_terminal_finish(mutation):
    value=task()
    if mutation=='missing':value['events']=value['events'][:-1]
    elif mutation=='wrong_revision':value['events'][-1]['payload']['taskRevision']=2
    elif mutation=='wrong_state':value['events'][-1]['payload']['state']='FAILED'
    elif mutation=='nonterminal':value=task('PAUSED')
    elif mutation=='stale_finish':value['events'].append({'sequence':4,'type':'state.transition','payload':{'state':'SUCCEEDED'}})
    else:value['events']=[e for e in value['events'] if e['type']!='LOCAL_RUN_SUCCEEDED']
    assert not collector.closure_status(value)['verified']
    assert not collector.closure_status(copy.deepcopy(value))['verified']


def original():
    return json.loads((BASE/'fixtures/task001-projection.json').read_text())


def test_duplicate_snapshot_references_are_not_new_http_requests():
    value=original();value['duplicate']=copy.deepcopy(value['plan'])
    result=collector.model_usage(value)
    assert result['uniqueModelCallRecords']==4 and result['recordReferences']==8
    assert result['providerReportedTotals']['total_tokens']==47638
    assert result['providerReportedTotals']['cached_tokens']==20352


def test_invalid_usage_is_not_provider_zero_or_full_valid_total():
    value=original();value['plan']['capabilities']['planningTrace'][0]['context']['model_call']['usage']['total_tokens']=-1
    result=collector.model_usage(value)
    assert result['providerUsageInvalidCalls']==1
    assert result['providerUsageValidCalls']==3
    assert result['providerReportedTotals']['total_tokens']==37472


def test_conflicting_same_call_id_is_explicit():
    value=original();value['duplicate']=copy.deepcopy(value['plan'])
    value['duplicate']['capabilities']['planningTrace'][0]['context']['model_call']['duration_ms']=1
    assert collector.model_usage(value)['conflictingCallIdentities']


def test_foreign_observation_is_rejected():
    value=original();observations=json.loads((BASE/'fixtures/task001-observations.json').read_text())
    assert not collector.identity_and_observations(value,observations)['errors']
    observations['records'][0]['robotId']='foreign'
    assert collector.identity_and_observations(value,observations)['errors']


def test_read_only_backup_matches_real_task001_snapshot():
    report=json.loads((BASE/'fixtures/task001-point-in-time-report.json').read_text())
    assert report['journal']['exactMatch'] and report['apiStableAcrossBackup']
    assert report['apiObservationsMatchBackup']
    assert report['apiClosure']['status']=='legacy_no_explicit_finish'
    assert not report['closureVerified'] and not report['taskSucceeded']
    assert report['physicalOutcomeUnknown'] and report['unknownRecoveryContractCoherent']
