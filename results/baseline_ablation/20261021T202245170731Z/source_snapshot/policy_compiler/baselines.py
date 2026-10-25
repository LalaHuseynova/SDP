"""Deterministic controls: broad ceiling copying and simple English keywords.

No method in this file sees annotations or contains dataset-specific identifiers.
"""

import re

from .effective import effective_permissions
from .references import evidence_excerpt, mentions as _mentions, recipient_mentions, resource_mentioned
from .task_compiler import assemble_policy
from .validator import validate_context, validate_policy


def _nonready(request, context, questions):
    return assemble_policy(dict(status="clarification_required", reads=[], actions=[],
                                ambiguities=questions, denial_reason=None), request, context)


def keyword_policy(request, context):
    """A deliberately small English parser, sharing revised deterministic assembly."""
    context = validate_context(context)
    resources = [r.resource_id for r in context.trusted_resource_metadata.resources
                 if resource_mentioned(request, r)]
    recipients, ambiguous = recipient_mentions(request, context)
    sends = bool(re.search(r"\b(send|email|slack|mail|forward|message|post|share|create|calendar)\b", request, re.I))
    tools = [t for t in context.available_tool_schemas if _mentions(request, t.tool.replace("_", " "))]
    if sends:
        if ambiguous:
            return _nonready(request, context, ambiguous)
        questions = []
        if not tools:
            questions.append(dict(field="channel", question="Which channel/tool should be used?", candidate_ids=[]))
        if not resources:
            questions.append(dict(field="resource", question="Which resource/project should be used?", candidate_ids=[]))
        if not recipients:
            questions.append(dict(field="recipient", question="Who is the recipient?", candidate_ids=[]))
        if questions:
            return _nonready(request, context, questions)
    count = 2 if re.search(r"\btwice\b", request, re.I) else 1
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    match = re.search(r"\b(one|two|three|four|five|\d+)\s+(times|copies|messages?|emails?)\b", request, re.I)
    if match:
        token = match.group(1).lower()
        count = words[token] if token in words else int(token)
    return assemble_policy(dict(
        status="ready", reads=[dict(resource_id=rid, basis="explicit") for rid in resources],
        actions=[dict(tool=t.tool, operation=t.operation, source_resource_ids=resources,
                      recipient_ids=[r["recipient_id"] for r in recipients], calls_per_recipient=count, basis="explicit")
                 for t in tools] if sends else [], ambiguities=[], denial_reason=None), request, context)


def ceiling_copy_policy(request, context):
    """Copy all eligible reads/operation scopes, with request-bound recipient IDs.

    Omit recipient-bearing actions when no unique recipient is mentioned, so this
    control can pass the policy contract rather than failing only on fake identities.
    Counts are greedily allocated in tool-list order under the aggregate ceiling.
    """
    context = validate_context(context)
    bounds = effective_permissions(context)
    recipients, ambiguous = recipient_mentions(request, context)
    if ambiguous:
        recipients = []
    tools = {(t.tool, t.operation): t for t in context.available_tool_schemas}
    actions, remaining = [], bounds.max_total_actions
    evidence = dict(basis="necessary_inference", request_excerpt=evidence_excerpt(request),
                    rationale="Deterministic ceiling-copy control; no semantic task minimization.")
    for op in bounds.operations:
        tool = tools[(op.tool, op.operation)]
        targets = [r for r in recipients if r["recipient_id"] in op.recipient_ids]
        if not remaining or (tool.requires_recipient and not targets):
            continue
        count = min(op.max_calls, remaining)
        actions.append(dict(tool=op.tool, operation=op.operation, resource_ids=op.resource_ids,
                            recipients=targets, argument_constraints=op.argument_constraints,
                            max_calls=count, evidence=evidence))
        remaining -= count
    return validate_policy(dict(
        schema_version="1", original_request=request, status="ready",
        reads=[dict(resource_id=rid, evidence=evidence) for rid in bounds.read_resource_ids],
        actions=actions, expiration=dict(condition="task_completion", max_duration_seconds=bounds.max_duration_seconds),
        ambiguities=[], denial_reason=None), request, context)
