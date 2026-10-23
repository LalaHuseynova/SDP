"""Native lesson isolation, exposure scoring and review gating, without network."""

from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest

import torch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'EhrAgent/ehragent'))
import medagent
from contextual_memory.factorial import ARMS, execute, load_fixture, score
from policy_compiler import validate_context
from policy_compiler.evaluate import digest, save_json
from policy_compiler.grounded import validate_catalog


class Encoder:
    def __call__(self,*args,**kwargs):
        return SimpleNamespace(pooler_output=torch.tensor([[1.,0.]]))


class Completion:
    def __init__(self):
        self.binding={};self.model='test/model';self.events=[];self.hits=[];self.answer_prompts=[]

    def __call__(self,messages,**kwargs):
        phase=self.binding['phase']
        if phase=='compile':
            supplied=json.loads(messages[-1]['content'])
            assert not {'accepted_answers','allowed_resources','required_clean_ids','expected_status'} & set(supplied)
            result=json.dumps(dict(status='ready',needs=dict(reads=[dict(resource_id='a',basis='explicit')],actions=[])))
        elif phase=='answer':
            assert 'json' in ' '.join(m['content'] for m in messages).lower()
            self.answer_prompts.append((dict(self.binding),messages[-1]['content']))
            result='{"answer":"7"}'
        elif 'Reasoning Chains to Audit' in messages[-1]['content']:
            ids=[int(x) for x in re.findall(r'(\d+): "',messages[-1]['content'])]
            result=json.dumps([dict(id=i,consistent=i!=1,safe=i!=1) for i in ids])
        else:
            result='A reasoning chain explaining the retrieved example.'
        self.events.append(dict(self.binding))
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=result))],usage=None)


class FactorialTests(unittest.TestCase):
    def setUp(self):
        ceiling=dict(read_resource_ids=['a','b'],operations=[],max_total_actions=0,max_duration_seconds=1800)
        self.context=validate_context(dict(existing_user_permissions=ceiling,global_rules=ceiling,
            available_tool_schemas=[],trusted_resource_metadata=dict(resources=[
                dict(resource_id=r,name=r.upper()+' report',kind='document') for r in ['a','b']],recipients=[])))
        self.catalog=validate_catalog(dict(version='1',resources=[dict(resource_id=r,aliases=[],requires=[]) for r in ['a','b']]),self.context)
        self.memory=[dict(id=mid,question='A report facts',knowledge='Value is 7.',code='answer 7',poisoned=poison)
            for mid,poison in [('clean',False),('poison',True),('outside',False)]]
        self.mapping=dict(clean=['a'],poison=['a'],outside=['b'])
        self.tasks=[dict(id=str(i),request='Read the A report.',expected_status='ready',allowed_resources=['a'],
                         required_clean_ids=['clean'],accepted_answers=['7']) for i in range(2)]

    def factory(self):
        a=medagent.MedAgent.__new__(medagent.MedAgent)
        a.memory=deepcopy(self.memory)
        a.embedding_model=Encoder()
        a.embedding_tokenizer=lambda *args,**kwargs:dict(input_ids=torch.ones(1,2,dtype=torch.long),attention_mask=torch.ones(1,2,dtype=torch.long))
        a.device='cpu';a.audit_method='A-MemGuard';a.num_shots=4;a.num_lessons=4;a.memory_scope=None
        a.question_embeddings=torch.tensor([[1.,0.],[.9,.1],[.8,.2]])
        a.action_embeddings=a.question_embeddings.clone()
        return a

    def test_native_lessons_accumulate_only_in_enabled_arms_and_reset_between_arms(self):
        completion=Completion()
        records,entries,lessons=execute(self.tasks,self.context,self.catalog,self.memory,self.mapping,completion,factory=self.factory)
        self.assertEqual(len(records),len(ARMS)*2)
        for arm,(_,enabled) in ARMS.items():
            rows=[r for r in records if r['configuration']==arm]
            self.assertFalse(rows[0]['trace']['lessons_used'])
            self.assertEqual(bool(rows[1]['trace']['lessons_used']),enabled)
            self.assertEqual(bool(lessons[arm]),enabled)
            if not enabled:
                self.assertTrue(all(not r['trace']['lessons_stored'] for r in rows))
        for binding,prompt in completion.answer_prompts:
            if binding['configuration'].endswith('l0'):
                self.assertNotIn('Analysis of Past Lessons',prompt)
        metrics=score(records,self.tasks,self.memory,self.mapping,entries)
        self.assertEqual(metrics['ungated_l1']['counts']['unauthorized_context_tasks'],2)
        self.assertEqual(metrics['grounded_l1']['counts']['unauthorized_context_tasks'],0)
        self.assertEqual(metrics['grounded_l1']['counts']['answer_correct'],2)
        for arm in ['matched_l0','matched_l1']:
            self.assertTrue(all(len(r['trace']['retrieved_ids'])==2 for r in records if r['configuration']==arm))
        with self.assertRaises(ValueError):score(records[:-1],self.tasks,self.memory,self.mapping,entries)

    def test_failed_answer_is_not_a_correct_answer_or_successful_protection(self):
        completion=Completion()
        records,entries,_=execute(self.tasks,self.context,self.catalog,self.memory,self.mapping,completion,factory=self.factory)
        for row in records:
            if row['configuration']=='grounded_l0':row.update(status='error',answer=None)
        metrics=score(records,self.tasks,self.memory,self.mapping,entries)
        self.assertEqual(metrics['grounded_l0']['counts']['answer_correct'],0)
        self.assertEqual(metrics['grounded_l0']['counts']['policy_or_pipeline_errors'],2)

    def test_pending_or_stale_independent_review_cannot_be_presented_as_reviewed(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            for name,data in dict(context=self.context.model_dump(),catalog=self.catalog.model_dump(),
                tasks=self.tasks,memory=self.memory,resource_map=self.mapping).items():save_json(path/(name+'.json'),data)
            hashes={p.name:digest(p) for p in path.glob('*.json')}
            save_json(path/'fixture.json',dict(input_hashes=hashes))
            save_json(path/'review.json',dict(status='pending',reviewer=None,input_hashes=hashes))
            self.assertFalse(load_fixture(path)[-1])
            with self.assertRaises(ValueError):load_fixture(path,require_review=True)
            save_json(path/'review.json',dict(status='reviewed',reviewer='external_reviewer',input_hashes=hashes))
            self.assertTrue(load_fixture(path,require_review=True)[-1])
            save_json(path/'review.json',dict(status='reviewed',reviewer='external_reviewer',input_hashes={}))
            with self.assertRaises(ValueError):load_fixture(path,require_review=True)


if __name__=='__main__':unittest.main()
