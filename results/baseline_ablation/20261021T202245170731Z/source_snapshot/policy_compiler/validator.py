"""Deterministic schema/ceiling checks; natural-language intent is not a proof."""

import json
import re
from typing import Any

from pydantic import ValidationError

from .schema import Ambiguity, CompilerContext, Evidence, TaskPolicy
from .references import display_reference


MAX_JSON_BYTES = 262144


class PolicyValidationError(ValueError):
    """Fail-closed validation error. Messages deliberately omit supplied content."""


class RecipientAmbiguityError(PolicyValidationError):
    """The compiler may downgrade this specific error to a no-grant clarification."""

    def __init__(self, reference, candidates):
        super().__init__("Recipient identity is ambiguous; clarification is required.")
        self.ambiguity = Ambiguity(
            field="recipient", question=f"Which recipient do you mean by '{display_reference(reference)}'?",
            candidate_ids=candidates,
        )


def _fail(message: str) -> None:
    raise PolicyValidationError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("Duplicate JSON keys are forbidden.")
        result[key] = value
    return result


def _invalid_constant(_value):
    _fail("Non-finite JSON numbers are forbidden.")


def load_json(text: str) -> Any:
    """Parse one bounded JSON document, never Markdown or permissive JSON."""
    if not isinstance(text, str):
        _fail("JSON input must be text.")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        _fail("JSON input must be valid UTF-8 text.")
    if size > MAX_JSON_BYTES:
        _fail("JSON input is invalid or exceeds the size limit.")
    try:
        return json.loads(text, object_pairs_hook=_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        _fail("Invalid JSON document (including duplicate keys or non-finite numbers).")


def _unique(values, label):
    if len(values) != len(set(values)):
        _fail(f"Duplicate {label} are forbidden.")


def _key(item):
    return item.tool, item.operation


def _typed(value):
    # Python considers True == 1; permission constraints must not.
    return type(value).__name__, json.dumps(value, allow_nan=False)


def _values(values):
    return {_typed(value) for value in values}


def _check_arguments(constraints, tool):
    for name, values in constraints.items():
        spec = tool.arguments.get(name)
        if spec is None:
            _fail("An argument constraint names an unknown tool argument.")
        types = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,)}
        if any(type(value) not in types[spec.value_type] for value in values):
            _fail("An argument constraint has an incorrect value type.")
        if len(_values(values)) != len(values):
            _fail("Duplicate argument constraint values are forbidden.")
        if spec.enum is not None and not _values(values) <= _values(spec.enum):
            _fail("An argument constraint exceeds the tool schema enum.")


def validate_context(context: CompilerContext | dict) -> CompilerContext:
    """Revalidate even model instances; callers cannot bypass checks with construct()."""
    try:
        raw = context.model_dump() if isinstance(context, CompilerContext) else context
        context = CompilerContext.model_validate(raw)
    except (ValidationError, ValueError, TypeError, RecursionError):
        _fail("Trusted context does not match the version-one schema.")
    metadata = context.trusted_resource_metadata
    resources = {r.resource_id for r in metadata.resources}
    recipients = {r.recipient_id for r in metadata.recipients}
    _unique([r.resource_id for r in metadata.resources], "resource identifiers")
    _unique([r.recipient_id for r in metadata.recipients], "recipient identifiers")
    if resources & recipients:
        _fail("Resource and recipient identifier namespaces must be disjoint.")
    _unique([_key(t) for t in context.available_tool_schemas], "tool/operation pairs")
    tools = {_key(t): t for t in context.available_tool_schemas}
    for tool in tools.values():
        for name, spec in tool.arguments.items():
            if spec.enum is not None:
                _check_arguments({name: spec.enum}, tool)
    for ceiling in (context.existing_user_permissions, context.global_rules):
        _unique(ceiling.read_resource_ids, "read identifiers")
        if not set(ceiling.read_resource_ids) <= resources:
            _fail("A read ceiling refers to unknown trusted metadata.")
        _unique([_key(op) for op in ceiling.operations], "ceiling operations")
        for operation in ceiling.operations:
            tool = tools.get(_key(operation))
            if tool is None:
                _fail("A ceiling refers to an unknown tool/operation.")
            _unique(operation.resource_ids, "operation resource identifiers")
            _unique(operation.recipient_ids, "operation recipient identifiers")
            if not set(operation.resource_ids) <= resources or not set(operation.recipient_ids) <= recipients:
                _fail("An operation ceiling refers to unknown trusted metadata.")
            if tool.requires_resource != bool(operation.resource_ids):
                _fail("A ceiling resource scope does not match its tool schema.")
            if tool.requires_recipient != bool(operation.recipient_ids):
                _fail("A ceiling recipient scope does not match its tool schema.")
            _check_arguments(operation.argument_constraints, tool)
    return context


def _check_evidence(evidence: Evidence, request: str):
    if evidence.basis == "uncertain":
        _fail("Uncertain permissions require clarification and cannot be granted.")
    if evidence.request_excerpt not in request:
        _fail("Permission evidence is not an exact excerpt of the original request.")


def validate_policy(policy: TaskPolicy | dict | str, original_request: str,
                    context: CompilerContext | dict) -> TaskPolicy:
    """Return a checked policy or raise; never silently repair an excessive grant."""
    context = validate_context(context)
    if isinstance(policy, str):
        policy = load_json(policy)
    try:
        raw = policy.model_dump() if isinstance(policy, TaskPolicy) else policy
        policy = TaskPolicy.model_validate(raw)
    except (ValidationError, ValueError, TypeError, RecursionError):
        _fail("Policy does not match the version-one schema.")
    if policy.original_request != original_request:
        _fail("Policy is not bound to the original authenticated request.")
    ceilings = (context.existing_user_permissions, context.global_rules)
    if policy.expiration.max_duration_seconds > min(c.max_duration_seconds for c in ceilings):
        _fail("Policy expiration exceeds a trusted ceiling.")
    metadata = context.trusted_resource_metadata
    known_ids = {r.resource_id for r in metadata.resources} | {r.recipient_id for r in metadata.recipients}
    for ambiguity in policy.ambiguities:
        _unique(ambiguity.candidate_ids, "ambiguity candidate identifiers")
        if not set(ambiguity.candidate_ids) <= known_ids:
            _fail("A clarification refers to unknown candidate identifiers.")
    if policy.status != "ready":
        if policy.reads or policy.actions:
            _fail("Clarification and denied results must contain no permission grants.")
        if policy.status == "clarification_required" and (not policy.ambiguities or policy.denial_reason is not None):
            _fail("A clarification requires questions and cannot carry a denial reason.")
        if policy.status == "denied" and (policy.denial_reason is None or policy.ambiguities):
            _fail("A denial requires a reason and cannot carry unresolved questions.")
        return policy
    if policy.ambiguities or policy.denial_reason is not None:
        _fail("A ready policy cannot contain unresolved questions or a denial reason.")
    _unique([r.resource_id for r in policy.reads], "policy read identifiers")
    for read in policy.reads:
        _check_evidence(read.evidence, original_request)
        if any(read.resource_id not in c.read_resource_ids for c in ceilings):
            _fail("A memory/resource read exceeds a trusted ceiling.")
    _unique([_key(a) for a in policy.actions], "policy operations")
    if sum(a.max_calls for a in policy.actions) > min(c.max_total_actions for c in ceilings):
        _fail("The aggregate action count exceeds a trusted ceiling.")
    tools = {_key(t): t for t in context.available_tool_schemas}
    recipients = {r.recipient_id: r for r in metadata.recipients}
    for action in policy.actions:
        _check_evidence(action.evidence, original_request)
        tool = tools.get(_key(action))
        if tool is None:
            _fail("Policy names an unknown tool/operation.")
        _unique(action.resource_ids, "policy action resource identifiers")
        _unique([r.recipient_id for r in action.recipients], "policy action recipient identifiers")
        if tool.requires_resource != bool(action.resource_ids):
            _fail("Policy resource scope does not match the tool schema.")
        if tool.requires_recipient != bool(action.recipients):
            _fail("Policy recipient scope does not match the tool schema.")
        _check_arguments(action.argument_constraints, tool)
        for recipient in action.recipients:
            if not re.search(r"(?<!\w)" + re.escape(recipient.request_reference) + r"(?!\w)", original_request):
                _fail("A recipient reference is not present in the original request.")
            reference = recipient.request_reference.casefold()
            matches = [r.recipient_id for r in recipients.values()
                       if reference in {s.casefold() for s in [r.recipient_id, r.name, *r.aliases]}]
            if len(matches) > 1:
                raise RecipientAmbiguityError(recipient.request_reference, matches)
            if matches != [recipient.recipient_id]:
                _fail("Recipient identity is unresolved; clarification is required.")
        for ceiling in ceilings:
            limit = next((op for op in ceiling.operations if _key(op) == _key(action)), None)
            if limit is None:
                _fail("A tool/operation exceeds a trusted ceiling.")
            if not set(action.resource_ids) <= set(limit.resource_ids):
                _fail("An action resource scope exceeds a trusted ceiling.")
            if not {r.recipient_id for r in action.recipients} <= set(limit.recipient_ids):
                _fail("An action recipient scope exceeds a trusted ceiling.")
            if action.max_calls > limit.max_calls:
                _fail("An operation action count exceeds a trusted ceiling.")
            for name, values in limit.argument_constraints.items():
                proposed = action.argument_constraints.get(name)
                if proposed is None or not _values(proposed) <= _values(values):
                    _fail("An argument constraint omits or exceeds a trusted restriction.")
    return policy
