"""Trusted catalog binding and fail-closed intent construction, without API calls."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from policy_compiler.grounded import (
    assemble_grounded, compile_grounded_policy, keyword_grounded_policy, validate_catalog,
)
from policy_compiler.validator import PolicyValidationError, validate_context

ROOT = Path(__file__).resolve().parents[1]


class GroundedCompilerTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((ROOT/'contextual_memory/fixtures/ehr5/context.json').read_text())
        self.catalog = json.loads((ROOT/'policy_compiler/evaluation/clinical_catalog.json').read_text())

    def ready(self, *ids):
        return dict(status='ready', needs=dict(reads=[dict(resource_id=r, basis='explicit') for r in ids], actions=[]))

    def test_development_queries_allow_aliases_and_add_transitive_dependencies(self):
        cases = json.loads((ROOT/'contextual_memory/fixtures/ehr5/tasks.json').read_text())
        for task in cases:
            roots = [r for r in task['allowed_tables'] if r != 'patient']
            policy = assemble_grounded(self.ready(*roots), task['template'], self.context, self.catalog)
            self.assertEqual(policy.status, 'ready')
            self.assertEqual({r.resource_id for r in policy.reads}, set(task['allowed_tables']))

    def test_dependency_outside_either_ceiling_denies_whole_task(self):
        for ceiling in ['global_rules', 'existing_user_permissions']:
            context = deepcopy(self.context)
            context[ceiling]['read_resource_ids'].remove('patient')
            policy = assemble_grounded(self.ready('medication'), 'Read prescriptions.', context, self.catalog)
            self.assertEqual(policy.status, 'denied')
            self.assertFalse(policy.reads or policy.actions)

    def test_cyclic_unknown_and_duplicate_catalog_entries_rejected(self):
        for kind in ['cycle', 'unknown', 'duplicate']:
            catalog = deepcopy(self.catalog)
            if kind == 'cycle': catalog['resources'][7]['requires'] = ['medication']
            if kind == 'unknown': catalog['resources'][0]['requires'] = ['invented']
            if kind == 'duplicate': catalog['resources'].append(catalog['resources'][0])
            with self.assertRaises(PolicyValidationError): validate_catalog(catalog, self.context)

    def test_status_specific_outputs_cannot_smuggle_grants(self):
        denied = dict(status='denied', denial_reason='Prohibited.')
        policy = assemble_grounded(denied, 'Read records.', self.context, self.catalog)
        self.assertEqual(policy.status, 'denied')
        for field in ['reads', 'actions', 'needs']:
            bad = dict(denied, **{field: []})
            with self.assertRaises(PolicyValidationError):
                assemble_grounded(bad, 'Read records.', self.context, self.catalog)
        clarification = dict(status='clarification_required', ambiguities=[dict(
            field='resource', question='Which resource?', candidate_ids=[])])
        policy = assemble_grounded(clarification, 'Read records.', self.context, self.catalog)
        self.assertFalse(policy.reads or policy.actions)

    def test_model_inference_does_not_bypass_source_binding_or_known_ids(self):
        policy = assemble_grounded(self.ready('medication'), 'Explain weather.', self.context, self.catalog)
        self.assertEqual(policy.status, 'clarification_required')
        with self.assertRaises(PolicyValidationError):
            assemble_grounded(self.ready('invented'), 'Read invented.', self.context, self.catalog)
        needs = self.ready('medication')
        needs['needs']['reads'].append(dict(resource_id='treatment', basis='necessary_inference'))
        policy = assemble_grounded(needs, 'Read prescriptions.', self.context, self.catalog)
        self.assertEqual(policy.status, 'clarification_required')
        self.assertFalse(policy.reads)

    def test_keyword_control_receives_same_aliases_and_dependencies(self):
        policy = keyword_grounded_policy('When was the last prescription?', self.context, self.catalog)
        self.assertEqual({r.resource_id for r in policy.reads}, {'medication', 'patient'})

    def test_compiler_prompt_contains_catalog_but_no_answers_or_labels(self):
        def completion(messages, **kwargs):
            supplied = json.loads(messages[-1]['content'])
            self.assertIn('resource_catalog', supplied)
            self.assertNotIn('allowed_tables', supplied)
            self.assertNotIn('answer', supplied)
            self.assertNotIn('required_reads', supplied)
            self.assertEqual(kwargs['response_format'], {'type':'json_object'})
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
                content=json.dumps(self.ready('medication'))))])
        policy = compile_grounded_policy('Read prescriptions.', **self.context,
                                         resource_catalog=self.catalog, completion=completion)
        self.assertEqual(policy.status, 'ready')


if __name__ == '__main__': unittest.main()
