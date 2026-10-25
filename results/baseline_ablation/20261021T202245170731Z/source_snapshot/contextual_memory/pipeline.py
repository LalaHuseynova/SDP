"""Optional gate -> fixed metadata retrieval -> existing A-MemGuard checker."""

from pathlib import Path
import re
from typing import Annotated

from pydantic import StringConstraints

from policy_compiler.schema import Identifier, StrictModel, Text
from policy_compiler.validator import load_json


MemoryID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


class MemoryMetadata(StrictModel):
    memory_id: MemoryID
    resource_id: Identifier
    title: Text


class LocalMemoryStore:
    """Index metadata and payloads are separate; read() is the payload boundary."""
    def __init__(self, index_path, payload_root):
        self.metadata = tuple(MemoryMetadata.model_validate(m) for m in load_json(Path(index_path).read_text()))
        self._ids = {m.memory_id for m in self.metadata}
        if len(self._ids) != len(self.metadata):
            raise ValueError("Duplicate memory identifiers.")
        self._root = Path(payload_root)
        self.accesses = []

    def read(self, memory_id):
        if memory_id not in self._ids:
            raise ValueError("Unknown memory identifier.")
        text = (self._root / (memory_id + ".txt")).read_text(encoding="utf-8")
        self.accesses.append(memory_id)
        return text


def rank_metadata(query, metadata, k):
    if type(k) is not int or not 1 <= k <= 20:
        raise ValueError("Retrieval k must be between 1 and 20.")
    tokens = set(re.findall(r"\w+", query.casefold()))
    def key(item):
        words = set(re.findall(r"\w+", item.title.casefold()))
        score = len(tokens & words) / max(1, len(words))
        return -score, item.memory_id
    return tuple(sorted(metadata, key=key)[:k])


class MemoryPipeline:
    def __init__(self, store, checker, *, k=4):
        self.store, self.checker, self.k = store, checker, k

    def run(self, original_request, *, gate=None, trace=None):
        """None explicitly selects the ungated baseline; callers must handle policy failures."""
        trace = trace if trace is not None else {}
        trace.update(eligible_ids=[], retrieved_ids=[], checker_input_ids=[], delivered_ids=[],
                     rejected_ids=[], delivered_bytes=0, checker_completed=False)
        eligible = gate.filter(self.store.metadata) if gate is not None else self.store.metadata
        trace["eligible_ids"] = [m.memory_id for m in eligible]
        selected = rank_metadata(original_request, eligible, self.k)
        trace["retrieved_ids"] = [m.memory_id for m in selected]
        texts = [self.store.read(m.memory_id) for m in selected]
        trace["checker_input_ids"] = list(trace["retrieved_ids"])
        result = self.checker.check(original_request, texts, list(range(len(texts))), method="llm")
        passed = result["consistent_memories"]
        failed = result["inconsistent_memories"]
        indices = [item["index"] for item in [*passed, *failed]]
        if any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(texts))):
            raise ValueError("Checker did not partition the retrieved records exactly once.")
        for item in [*passed, *failed]:
            if item["memory"] != texts[item["index"]]:
                raise ValueError("Checker returned a different memory payload.")
        trace["delivered_ids"] = [selected[item["index"]].memory_id for item in passed]
        trace["rejected_ids"] = [selected[item["index"]].memory_id for item in failed]
        trace["delivered_bytes"] = sum(len(item["memory"].encode("utf-8")) for item in passed)
        trace["checker_completed"] = True
        return dict(context="\n\n".join(item["memory"] for item in passed), checker_result=result, trace=trace)
