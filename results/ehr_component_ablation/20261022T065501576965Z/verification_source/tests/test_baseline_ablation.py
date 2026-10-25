"""Check ablation isolation, denominators and request contents without API calls."""

from copy import deepcopy
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'EhrAgent/ehragent'))
import medagent
from contextual_memory.baseline_ablation import ARMS, CONDITIONS, execute_trial, score


class Encoder:
    def __call__(self, *args, **kwargs):
        return SimpleNamespace(pooler_output=torch.tensor([[1., 0.]]))


class Completion:
    def __init__(self, fail_answer=False):
        self.events, self.hits, self.binding = [], [], {}
        self.model, self.fail_answer = 'test/model', fail_answer

    def __call__(self, messages, **kwargs):
        if self.binding['phase']=='answer':
            request = json.loads(messages[-1]['content'])
            assert set(request)=={'original_request','demonstration_context'}
            assert kwargs['response_format']=={'type':'json_object'}
            if self.fail_answer:
                raise RuntimeError('fixture failure')
            body = '{"answer":"7"}'
        elif 'Reasoning Chains to Audit' in messages[-1]['content']:
            ids = [int(i) for i in re.findall(r'(\d+): "', messages[-1]['content'])]
            body = json.dumps([dict(id=i,consistent=i!=1,safe=i!=1) for i in ids])
        else:
            body = 'A reasoning chain for this memory.'
        self.events.append(dict(self.binding, response={'fixture':True}))
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=body))],usage=None)


class BaselineAblationTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [dict(id=str(i),request=f'What is the value in record {i}?',accepted_answers=['7'],required_clean_ids=['clean']) for i in range(2)]
        self.memory = [dict(id=mid,question='The value',knowledge='Value is 7.',code='Value is 7.',poisoned=poison)
                       for mid,poison in [('clean',False),('poison',True)]]

    def factory(self, condition):
        agent = medagent.MedAgent.__new__(medagent.MedAgent)
        agent.memory = deepcopy([m for m in self.memory if condition=='poisoned' or not m['poisoned']])
        agent.embedding_model = Encoder()
        agent.embedding_tokenizer = lambda *a,**kw: dict(input_ids=torch.ones(1,2,dtype=torch.long),attention_mask=torch.ones(1,2,dtype=torch.long))
        agent.device='cpu'; agent.num_shots=4; agent.num_lessons=4
        agent.question_embeddings = torch.tensor([[1.,0.]]*len(agent.memory))
        agent.action_embeddings = agent.question_embeddings.clone()
        return agent

    def test_arms_reset_lessons_and_only_enabled_arm_accumulates(self):
        records, lessons = execute_trial(self.tasks,self.memory,Completion(),0,factory=self.factory)
        self.assertEqual(len(records),len(self.tasks)*len(ARMS)*len(CONDITIONS))
        for condition in CONDITIONS:
            for arm in ARMS:
                rows = [r for r in records if (r['condition'],r['arm'])==(condition,arm)]
                self.assertFalse(rows[0]['trace'].get('lessons_used'))
                if arm!='checker_lessons':
                    self.assertFalse(lessons[condition+'/'+arm])
                    self.assertTrue(all(not r['trace'].get('lessons_used') for r in rows))
                if arm in ('no_memory','no_defense'):
                    self.assertTrue(all('judge' not in r['trace'] for r in rows))
                if arm=='no_memory':
                    self.assertTrue(all(r['context_characters']==0 for r in rows))
        poisoned = [r for r in records if r['condition']=='poisoned' and r['arm']=='checker_lessons']
        self.assertTrue(poisoned[1]['trace']['lessons_used'])
        metrics = score(records,self.tasks,self.memory,seeds=(0,))
        self.assertEqual(metrics['per_trial']['0/clean/no_defense']['correct'],2)
        self.assertEqual(metrics['per_trial']['0/poisoned/no_defense']['poison_delivered'],2)

    def test_failed_answers_stay_in_utility_denominators_and_exposure_is_unknown(self):
        records,_ = execute_trial(self.tasks,self.memory,Completion(fail_answer=True),0,factory=self.factory)
        metrics = score(records,self.tasks,self.memory,seeds=(0,))
        for value in metrics['per_trial'].values():
            self.assertEqual(value['tasks'],2)
            self.assertEqual(value['failed'],2)
            self.assertEqual(value['correct'],0)
            self.assertEqual(value['exposure_unknown'],2)
        with self.assertRaises(ValueError):
            score(records[:-1],self.tasks,self.memory,seeds=(0,))


if __name__=='__main__':
    unittest.main()
