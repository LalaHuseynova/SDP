"""Trusted metadata authorization before payload reads or reasoning checks."""

import time

from policy_compiler.validator import validate_context, validate_policy


class PolicyMemoryGate:
    def __init__(self, policy, original_request, context, *, clock=time.monotonic):
        context = validate_context(context)
        checked = validate_policy(policy, original_request, context)
        self._allowed = frozenset(r.resource_id for r in checked.reads) if checked.status == "ready" else frozenset()
        self._known = frozenset(r.resource_id for r in context.trusted_resource_metadata.resources)
        self._clock = clock
        self._expires = clock() + checked.expiration.max_duration_seconds
        self._completed = False

    def filter(self, metadata):
        if self._completed or self._clock() >= self._expires:
            return ()
        return tuple(m for m in metadata if m.resource_id in self._known and m.resource_id in self._allowed)

    def complete(self):
        self._completed = True
