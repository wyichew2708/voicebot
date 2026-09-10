import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from voicebot.call import router
from voicebot.routing_eval import compare, load_cases


@pytest.fixture
def sample():
    cases,digest=load_cases(Path(__file__).parent/'eval/routing.jsonl')
    base={'dataset_sha256':digest,'prompt_version':router.PROMPT_VERSION,
          'system_prompt_sha256':hashlib.sha256(router.system_prompt().encode()).hexdigest(),
          'max_tokens':router.MAX_TOKENS,'mode':'live','timeout_ms':1500,
          'model':'baseline-fixture','revision':'rev1','runtime':'runtime1',
          'quantization':'test','declared_state':'warm',
          'predictions':[{'id':c['id'],'reply':c['expected'],'wall_ms':100} for c in cases]}
    candidate=copy.deepcopy(base);candidate['model']='candidate-fixture'
    for r in candidate['predictions']:r['wall_ms']=90
    return cases,digest,base,candidate


def test_perfect_fixture_can_pass_routing_but_never_authorize_promotion(sample):
    result=compare(*sample)
    assert result['routing_checks_passed']
    assert result['latency_delta_ms'] == {'p50':-10,'p95':-10}
    assert result['promotion_eligible'] is False
    assert result['case_splits'] == {'review':66}


def test_critical_error_blocks_even_with_permissive_accuracy_floor(sample):
    cases,digest,b,c=sample
    row=next(r for r in c['predictions'] if r['id']=='dnc-en');row['reply']='affirm'
    b['predictions'][0]['reply']='unclear'  # same aggregate score, different errors
    result=compare(cases,digest,b,c,min_accuracy=0)
    assert not result['routing_checks_passed']
    assert 'dnc-en' in result['critical_failure_ids']
    assert 'dnc-en' in result['regression_ids']
    assert 'price-en' in result['fixed_ids']


@pytest.mark.parametrize('field,value',[('dataset_sha256','wrong'),('system_prompt_sha256','wrong'),
                                     ('timeout_ms',2000),('max_tokens',100),('prompt_version','old')])
def test_mismatched_conditions_reject_report(sample,field,value):
    sample[-1][field]=value
    with pytest.raises(ValueError):compare(*sample)


def test_unknown_provenance_and_missing_timings_block_evidence(sample):
    sample[-1]['revision']='unknown'
    del sample[-1]['predictions'][0]['wall_ms']
    result=compare(*sample)
    assert not result['routing_checks_passed']
    assert result['latency_delta_ms'] is None
    assert len(result['evidence_blockers']) == 2


def test_raw_predictions_override_fabricated_summary_and_missing_is_failure(sample):
    sample[-1]['overall']={'accuracy':1,'wall_ms':{'p95':1}}
    sample[-1]['predictions']=[]
    result=compare(*sample)
    assert result['candidate']['overall']['accuracy']==0
    assert result['candidate']['overall']['statuses']=={'missing':66}
    assert not result['routing_checks_passed']


def test_cold_and_warm_cannot_be_compared_for_latency(sample):
    sample[-1]['declared_state']='cold'
    result=compare(*sample)
    assert result['latency_delta_ms'] is None
    assert not result['routing_checks_passed']


@pytest.mark.parametrize('value',[float('nan'),-1,1.1,True])
def test_invalid_thresholds_rejected(sample,value):
    with pytest.raises(ValueError):compare(*sample,min_accuracy=value)


def test_comparison_cli_emits_blocked_exit_and_report(sample,tmp_path):
    cases,digest,b,c=sample
    c['revision']='unknown'
    paths=[]
    for name,report in [('baseline',b),('candidate',c)]:
        path=tmp_path/(name+'.json');path.write_text(json.dumps(report));paths.append(str(path))
    output=tmp_path/'comparison.json'
    command=[sys.executable,str(Path(__file__).parents[1]/'scripts/compare_routing.py'),*paths,'--output',str(output)]
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode==1
    assert json.loads(output.read_text())['promotion_eligible'] is False
