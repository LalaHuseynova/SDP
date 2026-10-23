"""Revised task-needs inference and trusted policy construction, offline."""

from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from policy_compiler import CompilationError, PolicyValidationError, compile_task_policy, validate_context
from policy_compiler.effective import effective_permissions
from policy_compiler.task_compiler import assemble_policy
from policy_compiler.validator import RecipientAmbiguityError, validate_policy


FIXTURES = Path(__file__).resolve().parents[1] / "policy_compiler/evaluation"
REQUEST = "Send Alice the Project X update on Slack."


class RevisedCompilerTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((FIXTURES / "context.json").read_text())
        self.needs = dict(status="ready", reads=[], actions=[dict(
            tool="slack", operation="send_message", source_resource_ids=["project_x_update"],
            recipient_ids=["alice"], calls_per_recipient=1, basis="explicit")],
            ambiguities=[], denial_reason=None)

    def test_intersection_has_no_user_only_resources_or_larger_user_limits(self):
        bounds = effective_permissions(self.context)
        self.assertNotIn("restricted_roadmap", bounds.read_resource_ids)
        self.assertEqual(bounds.max_duration_seconds, 3600)
        self.assertEqual(bounds.max_total_actions, 5)
        self.assertEqual([op.max_calls for op in bounds.operations], [3, 3])
        self.assertTrue(all(op.argument_constraints == {"format": ["plain"]} for op in bounds.operations))

    def test_disjoint_argument_constraints_remove_operation(self):
        self.context["existing_user_permissions"]["operations"][0]["argument_constraints"] = {"format": ["markdown"]}
        self.assertEqual([op.tool for op in effective_permissions(self.context).operations], ["email"])

    def test_empty_recipient_or_resource_intersection_removes_operation(self):
        for field, values in (("recipient_ids", ["bob"]), ("resource_ids", ["project_y_update"])):
            context = deepcopy(self.context)
            context["existing_user_permissions"]["operations"][0][field] = values
            context["global_rules"]["operations"][0][field] = ["alice"] if field == "recipient_ids" else ["project_x_update"]
            self.assertNotIn("slack", [op.tool for op in effective_permissions(context).operations])

    def test_assembly_adds_only_declared_source_reads_and_trusted_constraints(self):
        result = assemble_policy(self.needs, REQUEST, self.context)
        self.assertEqual([r.resource_id for r in result.reads], ["project_x_update"])
        self.assertEqual(result.actions[0].max_calls, 1)
        self.assertEqual(result.actions[0].argument_constraints, {"format": ["plain"]})
        self.assertEqual(result.expiration.max_duration_seconds, 3600)

    def test_long_valid_requests_keep_full_request_and_bounded_verbatim_evidence(self):
        for size in (4001, 16000):
            for request in (" " * (size - len(REQUEST)) + REQUEST,
                            REQUEST + " " * (size - len(REQUEST))):
                with self.subTest(size=size, trailing=request.endswith(" ")):
                    result = assemble_policy(self.needs, request, self.context)
                    self.assertEqual(result.status, "ready")
                    self.assertEqual(result.original_request, request)
                    for grant in [*result.reads, *result.actions]:
                        self.assertTrue(grant.evidence.request_excerpt.strip())
                        self.assertLessEqual(len(grant.evidence.request_excerpt), 4000)
                        self.assertIn(grant.evidence.request_excerpt, request)

    def test_invalid_requests_rejected_at_assembly_boundary(self):
        for request in (None, 42, " " * 10, "x" * 16001):
            with self.subTest(request_type=type(request).__name__), self.assertRaises(PolicyValidationError):
                assemble_policy(self.needs, request, self.context)

    def test_long_ambiguous_alias_yields_bounded_no_grant_question(self):
        alias = "A" * 4000
        for person in self.context["trusted_resource_metadata"]["recipients"]:
            if person["recipient_id"].startswith("casey_"):
                person["aliases"] = [alias]
        self.needs["actions"][0]["recipient_ids"] = ["casey_patel"]
        result = assemble_policy(self.needs, f"Send {alias} the Project X update on Slack.", self.context)
        self.assertEqual(result.status, "clarification_required")
        self.assertFalse(result.reads or result.actions)
        self.assertLess(len(result.ambiguities[0].question), 200)
        self.assertEqual(set(result.ambiguities[0].candidate_ids), {"casey_patel", "casey_morgan"})

    def test_original_validator_handles_long_ambiguous_alias_with_typed_error(self):
        candidate = assemble_policy(self.needs, REQUEST, self.context).model_dump()
        alias = "A" * 4000
        for person in self.context["trusted_resource_metadata"]["recipients"]:
            if person["recipient_id"].startswith("casey_"):
                person["aliases"] = [alias]
        request = f"Send {alias} the Project X update on Slack."
        candidate["original_request"] = request
        candidate["actions"][0]["recipients"] = [dict(recipient_id="casey_patel", request_reference=alias)]
        for grant in candidate["reads"] + candidate["actions"]:
            grant["evidence"]["request_excerpt"] = "Send"
        with self.assertRaises(RecipientAmbiguityError) as raised:
            validate_policy(candidate, request, self.context)
        self.assertLess(len(raised.exception.ambiguity.question), 200)

    def test_counts_are_per_recipient_and_not_silently_clamped(self):
        request = "Send the Project X update to Alice and Bob on Slack, once each."
        action = self.needs["actions"][0]
        action.update(recipient_ids=["alice", "bob"])
        result = assemble_policy(self.needs, request, self.context)
        self.assertEqual(result.actions[0].max_calls, 2)
        action["calls_per_recipient"] = 2
        result = assemble_policy(self.needs, request, self.context)
        self.assertEqual(result.status, "denied")
        self.assertFalse(result.actions or result.reads)

    def test_global_read_and_unavailable_operation_are_denied_without_grants(self):
        needs = dict(status="ready", reads=[dict(resource_id="restricted_roadmap", basis="explicit")],
                     actions=[], ambiguities=[], denial_reason=None)
        self.assertEqual(assemble_policy(needs, "Read the restricted roadmap.", self.context).status, "denied")
        self.needs["actions"][0].update(tool="calendar", operation="create_event")
        self.assertEqual(assemble_policy(self.needs, REQUEST, self.context).status, "denied")

    def test_ambiguous_and_uncertain_needs_remove_all_grants(self):
        request = "Send Casey the Project X update on Slack."
        self.needs["actions"][0].update(recipient_ids=["casey_patel"])
        result = assemble_policy(self.needs, request, self.context)
        self.assertEqual(result.status, "clarification_required")
        self.assertEqual(set(result.ambiguities[0].candidate_ids), {"casey_patel", "casey_morgan"})
        self.needs["actions"][0]["basis"] = "uncertain"
        result = assemble_policy(self.needs, request, self.context)
        self.assertEqual(result.status, "clarification_required")
        self.assertFalse(result.actions or result.reads)

    def test_unknown_duplicate_and_fabricated_needs_are_not_repaired(self):
        for mutate in (
            lambda n: n["actions"][0].update(source_resource_ids=["invented"]),
            lambda n: n["actions"].append(deepcopy(n["actions"][0])),
            lambda n: n["actions"][0].update(request_excerpt="Different request"),
            lambda n: n.update(status="denied", denial_reason="No"),
        ):
            needs = deepcopy(self.needs)
            mutate(needs)
            with self.assertRaises(PolicyValidationError):
                assemble_policy(needs, REQUEST, self.context)

    def test_model_receives_effective_context_not_duplicate_ceilings_or_annotations(self):
        def completion(messages, **kwargs):
            supplied = json.loads(messages[-1]["content"])
            self.assertEqual(supplied["effective_permissions"]["max_duration_seconds"], 3600)
            self.assertNotIn("existing_user_permissions", supplied)
            self.assertNotIn("global_rules", supplied)
            self.assertNotIn("required_reads", json.dumps(messages))
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                message=SimpleNamespace(content=json.dumps(self.needs)))])
        self.assertEqual(compile_task_policy(REQUEST, **self.context, completion=completion).status, "ready")

    def test_missing_project_or_channel_requires_clarification(self):
        for request, topic in (("Send Alice the project update on Slack.", "resource"),
                               ("Send Alice the Project X update.", "channel")):
            result = assemble_policy(self.needs, request, self.context)
            self.assertEqual(result.status, "clarification_required")
            self.assertEqual(result.ambiguities[0].field, topic)
            self.assertFalse(result.reads or result.actions)

    def test_recipient_references_come_from_request_not_generated_full_name(self):
        result = assemble_policy(self.needs, REQUEST, self.context)
        self.assertEqual(result.actions[0].recipients[0].request_reference, "Alice")
        result = assemble_policy(self.needs, "Send Alice Chen the Project X update on Slack.", self.context)
        self.assertEqual(result.actions[0].recipients[0].request_reference, "Alice Chen")

    def test_invalid_or_truncated_task_needs_cannot_become_policy(self):
        def completion(messages, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="length",
                message=SimpleNamespace(content=json.dumps(self.needs)))])
        with self.assertRaises(CompilationError):
            compile_task_policy(REQUEST, **self.context, completion=completion)
        invalid = deepcopy(self.needs)
        invalid["expiration"] = {"max_duration_seconds": 7200}
        with self.assertRaises(PolicyValidationError):
            assemble_policy(invalid, REQUEST, self.context)

    def test_cli_defaults_to_revision_and_preserves_original_selection(self):
        from policy_compiler.__main__ import main
        policy = assemble_policy(self.needs, REQUEST, self.context)
        args = ["--request", REQUEST, "--context", str(FIXTURES / "context.json")]
        with patch("policy_compiler.__main__.compile_task_policy", return_value=policy) as revised, patch(
                "policy_compiler.__main__.compile_policy", side_effect=AssertionError("Wrong method")), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
            self.assertEqual(revised.call_args.args[0], REQUEST)
        with patch("policy_compiler.__main__.compile_policy", return_value=policy) as original, patch(
                "policy_compiler.__main__.compile_task_policy", side_effect=AssertionError("Wrong method")), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args + ["--method", "original"]), 0)
            self.assertEqual(original.call_args.args[0], REQUEST)


if __name__ == "__main__":
    unittest.main()
