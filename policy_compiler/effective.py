"""Calculate the intersection of structured ceilings without asking the LLM."""

import json

from .schema import CompilerContext, PermissionCeiling
from .validator import validate_context


def _values(values):
    return {(type(v).__name__, json.dumps(v)): v for v in values}


def effective_permissions(context: CompilerContext | dict) -> PermissionCeiling:
    context = validate_context(context)
    user, global_rules = context.existing_user_permissions, context.global_rules
    global_ops = {(op.tool, op.operation): op for op in global_rules.operations}
    tools = {(t.tool, t.operation): t for t in context.available_tool_schemas}
    operations = []
    for left in user.operations:
        key = left.tool, left.operation
        right = global_ops.get(key)
        if right is None:
            continue
        constraints = {}
        impossible = False
        for name in sorted(set(left.argument_constraints) | set(right.argument_constraints)):
            a, b = left.argument_constraints.get(name), right.argument_constraints.get(name)
            if a is None or b is None:
                constraints[name] = a if b is None else b
            else:
                right_keys = _values(b)
                constraints[name] = [v for k, v in _values(a).items() if k in right_keys]
                if not constraints[name]:
                    impossible = True
        resources = [r for r in left.resource_ids if r in right.resource_ids]
        recipients = [r for r in left.recipient_ids if r in right.recipient_ids]
        tool = tools[key]
        if impossible or (tool.requires_resource and not resources) or (tool.requires_recipient and not recipients):
            continue
        operations.append(dict(
            tool=left.tool, operation=left.operation, resource_ids=resources,
            recipient_ids=recipients, argument_constraints=constraints,
            max_calls=min(left.max_calls, right.max_calls)))
    return PermissionCeiling.model_validate(dict(
        read_resource_ids=[r for r in user.read_resource_ids if r in global_rules.read_resource_ids],
        operations=operations, max_total_actions=min(user.max_total_actions, global_rules.max_total_actions),
        max_duration_seconds=min(user.max_duration_seconds, global_rules.max_duration_seconds)))
