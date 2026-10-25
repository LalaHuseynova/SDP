"""Apply a task policy to externally annotated EHR demonstration/lesson resources."""

from types import SimpleNamespace

from .gate import PolicyMemoryGate


class EHRMemoryScope:
    def __init__(self, policy, original_request, context, resource_map):
        self.gate = PolicyMemoryGate(policy, original_request, context)
        # These table labels are trusted sidecar metadata, never read from poisoned text.
        self._resources = {mid: frozenset(resources) for mid, resources in resource_map.items()}
        self._metadata = tuple(SimpleNamespace(resource_id=r) for r in sorted(
            {r for resources in self._resources.values() for r in resources}))

    def eligible_indices(self, memory_ids):
        allowed = {m.resource_id for m in self.gate.filter(self._metadata)}
        return [i for i, mid in enumerate(memory_ids)
                if self._resources.get(mid) and self._resources[mid] <= allowed]

    def complete(self):
        self.gate.complete()
