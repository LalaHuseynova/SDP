"""Small, frozen, annotated diagnostic evaluation. No tool execution or model judge."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys
import time
from typing import Literal

from pydantic import Field, ValidationError

from .compiler import CompilationError, compile_policy
from .task_compiler import compile_task_policy
from .schema import Count, Identifier, Request, StrictModel, TaskPolicy, Text
from .references import evidence_excerpt
from .validator import PolicyValidationError, load_json, validate_context, validate_policy


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "evaluation"
MAX_CALLS = 20
MAX_OUTPUT_TOKENS = 2048
MAX_INPUT_TOKEN_BOUND = 32000
TOPIC_TERMS = {
    "recipient": ("recipient", "which casey", "who", "person", "identity"),
    "resource": ("project", "resource", "which update"),
    "channel": ("channel", "slack", "email", "platform", "tool", "method"),
}


class ExpectedAction(StrictModel):
    tool: Identifier
    operation: Identifier
    resource_ids: list[Identifier]
    recipient_ids: list[Identifier]
    max_calls: Count


class ExpectedPolicy(StrictModel):
    status: Literal["ready", "clarification_required", "denied"]
    required_reads: list[Identifier]
    optional_reads: list[Identifier]
    read_basis: dict[Identifier, Literal["explicit", "necessary_inference"]]
    actions: list[ExpectedAction]
    clarification_topics: list[Literal["recipient", "resource", "channel"]]


class EvaluationCase(StrictModel):
    id: Identifier
    category: Identifier
    request: Request
    expected: ExpectedPolicy


class EvaluationSuite(StrictModel):
    suite_version: Literal["1"]
    annotation_provenance: Text
    cases: list[EvaluationCase] = Field(min_length=1, max_length=MAX_CALLS)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def display_path(path):
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def oracle_policy(case, context):
    """Check annotation expressibility only. This oracle never goes to the LLM."""
    expected = case.expected
    request = case.request
    actions = []
    for action in expected.actions:
        recipients = []
        for recipient_id in action.recipient_ids:
            recipient = next(r for r in context.trusted_resource_metadata.recipients
                             if r.recipient_id == recipient_id)
            references = [r for r in [recipient.name, *recipient.aliases, recipient.recipient_id]
                          if re.search(r"(?<!\w)" + re.escape(r) + r"(?!\w)", request)]
            if not references:
                raise ValueError("An annotated recipient has no request reference.")
            recipients.append(dict(recipient_id=recipient_id,
                                   request_reference=max(references, key=len)))
        constraints = {}
        for ceiling in (context.existing_user_permissions, context.global_rules):
            limit = next(op for op in ceiling.operations
                         if (op.tool, op.operation) == (action.tool, action.operation))
            for name, values in limit.argument_constraints.items():
                constraints[name] = (values if name not in constraints else
                                     [v for v in constraints[name] if v in values])
        actions.append(dict(
            tool=action.tool, operation=action.operation,
            resource_ids=action.resource_ids, recipients=recipients,
            argument_constraints=constraints, max_calls=action.max_calls,
            evidence=dict(basis="explicit", request_excerpt=evidence_excerpt(request),
                          rationale="Annotation expressibility check.")))
    return dict(
        schema_version="1", original_request=request, status=expected.status,
        reads=[dict(resource_id=rid, evidence=dict(
            basis=expected.read_basis[rid], request_excerpt=evidence_excerpt(request),
            rationale="Annotation expressibility check."))
            for rid in expected.required_reads + expected.optional_reads],
        actions=actions,
        expiration=dict(condition="task_completion", max_duration_seconds=min(
            context.existing_user_permissions.max_duration_seconds,
            context.global_rules.max_duration_seconds)),
        ambiguities=[dict(field=topic, question=f"Which {topic} do you mean?", candidate_ids=[])
                     for topic in expected.clarification_topics],
        denial_reason="Forbidden by the supplied ceilings." if expected.status == "denied" else None,
    )


def read_suite(context_path, cases_path):
    context = validate_context(load_json(Path(context_path).read_text(encoding="utf-8")))
    try:
        suite = EvaluationSuite.model_validate(load_json(Path(cases_path).read_text(encoding="utf-8")))
    except ValidationError:
        raise ValueError("Evaluation cases do not match the annotation schema.") from None
    if len({c.id for c in suite.cases}) != len(suite.cases):
        raise ValueError("Duplicate evaluation case identifiers.")
    for case in suite.cases:
        expected = case.expected
        reads = expected.required_reads + expected.optional_reads
        if len(reads) != len(set(reads)) or set(reads) != set(expected.read_basis):
            raise ValueError("Read annotations overlap, repeat, or lack evidence labels.")
        if expected.status != "ready" and (reads or expected.actions):
            raise ValueError("Non-ready annotations cannot grant permissions.")
        if bool(expected.clarification_topics) != (expected.status == "clarification_required"):
            raise ValueError("Clarification annotations must specify their topics.")
        validate_policy(oracle_policy(case, context), case.request, context)
    return context, suite


def assess(case, policy):
    """Score the returned policy against annotations, never against model reasoning."""
    expected = case.expected
    over, under, basis_errors = [], [], []
    status_correct = policy is not None and policy.status == expected.status
    reads = {r.resource_id: r for r in policy.reads} if policy else {}
    allowed = set(expected.required_reads + expected.optional_reads)
    for rid in sorted(set(reads) - allowed):
        over.append(f"read:{rid}")
    for rid in sorted(set(expected.required_reads) - set(reads)):
        under.append(f"read:{rid}")
    for rid in sorted(set(reads) & allowed):
        if reads[rid].evidence.basis != expected.read_basis[rid]:
            basis_errors.append(f"read:{rid}")
    actual_actions = {(a.tool, a.operation): a for a in policy.actions} if policy else {}
    expected_actions = {(a.tool, a.operation): a for a in expected.actions}
    for key in sorted(set(actual_actions) - set(expected_actions)):
        over.append("operation:" + ".".join(key))
    for key in sorted(set(expected_actions) - set(actual_actions)):
        under.append("operation:" + ".".join(key))
    for key in sorted(set(actual_actions) & set(expected_actions)):
        actual, target = actual_actions[key], expected_actions[key]
        prefix = ".".join(key)
        for label, observed, wanted in (
            ("resource", set(actual.resource_ids), set(target.resource_ids)),
            ("recipient", {r.recipient_id for r in actual.recipients}, set(target.recipient_ids)),
        ):
            over.extend(f"{prefix}:{label}:{v}" for v in sorted(observed - wanted))
            under.extend(f"{prefix}:{label}:{v}" for v in sorted(wanted - observed))
        if actual.max_calls > target.max_calls:
            over.append(f"{prefix}:max_calls:{actual.max_calls}>{target.max_calls}")
        elif actual.max_calls < target.max_calls:
            under.append(f"{prefix}:max_calls:{actual.max_calls}<{target.max_calls}")
        if actual.evidence.basis != "explicit":
            basis_errors.append("operation:" + prefix)
    # This is a documented lexical screen, not a semantic judgment of question quality.
    questions = " ".join(f"{a.field} {a.question}" for a in policy.ambiguities).casefold() if policy else ""
    missing_topics = [topic for topic in expected.clarification_topics
                      if not any(term in questions for term in TOPIC_TERMS[topic])]
    scope_correct = bool(status_correct and not over and not under)
    return dict(
        status_correct=bool(status_correct), scope_correct=scope_correct,
        full_annotation_match=bool(scope_correct and not basis_errors and not missing_topics),
        overgrants=over, undergrants=under, basis_errors=basis_errors,
        clarification_topics_missing=missing_topics,
    )


def summarize(records, suite):
    by_id = {r["id"]: r for r in records}
    if len(by_id) != len(records) or set(by_id) != {c.id for c in suite.cases}:
        raise ValueError("Reporting requires exactly one record for every annotated case.")
    metrics = dict(total_cases=len(suite.cases), outcomes=dict(Counter(r["outcome"] for r in records)),
                   returned_statuses=dict(Counter(r["policy"]["status"] for r in records if r["policy"])),
                   expected_statuses=dict(Counter(c.expected.status for c in suite.cases)))
    assessments = []
    for case in suite.cases:
        record = by_id[case.id]
        policy = TaskPolicy.model_validate(record["policy"]) if record["policy"] else None
        record["assessment"] = assess(case, policy)
        assessments.append(record["assessment"])
    for key in ("status_correct", "scope_correct", "full_annotation_match"):
        metrics[key] = sum(a[key] for a in assessments)
    for key in ("overgrants", "undergrants", "basis_errors", "clarification_topics_missing"):
        metrics["cases_with_" + key] = sum(bool(a[key]) for a in assessments)
    metrics["rejection_reasons"] = dict(Counter(r["error"] for r in records if r.get("error")))
    metrics["by_category"] = {}
    for category in sorted({c.category for c in suite.cases}):
        members = [by_id[c.id] for c in suite.cases if c.category == category]
        metrics["by_category"][category] = dict(
            total=len(members), scope_correct=sum(r["assessment"]["scope_correct"] for r in members))
    return metrics


def validated_metrics(context, suite, records, events=None):
    """Recompute from saved outputs, checking binding and validation before scoring."""
    by_id = {r["id"]: r for r in records}
    for case in suite.cases:
        record = by_id.get(case.id)
        if record is None or record["request"] != case.request or record["category"] != case.category:
            raise ValueError("Saved records do not match the annotated requests.")
        if record["outcome"] not in {"accepted", "validation_error", "api_error", "compilation_error"}:
            raise ValueError("Unknown saved outcome.")
        if (record["outcome"] == "accepted") != (record["policy"] is not None):
            raise ValueError("Saved outcomes and policy presence disagree.")
        if record["policy"] is not None:
            validate_policy(record["policy"], case.request, context)
    metrics = summarize(records, suite)
    if events is None:
        return metrics
    event_ids = [e["case_id"] for e in events]
    if len(set(event_ids)) != len(event_ids) or not set(event_ids) <= set(by_id):
        raise ValueError("Saved API events repeat or name unknown cases.")
    for event in events:
        if event["status"] not in {"ok", "api_error"}:
            raise ValueError("Saved API event is incomplete or has an unknown status.")
        supplied = load_json(event["messages"][-1]["content"])
        if supplied["original_request"] != by_id[event["case_id"]]["request"]:
            raise ValueError("Saved API event does not match its request.")
        if (event["status"] == "api_error") != (by_id[event["case_id"]]["outcome"] == "api_error"):
            raise ValueError("Saved API and record outcomes disagree.")
    if any(r["outcome"] != "compilation_error" and r["id"] not in event_ids for r in records):
        raise ValueError("Saved record is missing its API event.")
    metrics["api_calls"] = len(events)
    metrics["usage"] = {key: sum((e.get("usage") or {}).get(key, 0) or 0 for e in events)
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    metrics["api_wall_seconds"] = round(sum(e["wall_seconds"] for e in events), 3)
    metrics["requested_models"] = dict(Counter(e["requested_model"] for e in events))
    metrics["returned_models"] = dict(Counter(e["returned_model"] for e in events if e.get("returned_model")))
    return metrics


def report(run, suite, records, events):
    metadata = load_json((run / "metadata.json").read_text())
    method = metadata.get("method", "original")
    context = validate_context(load_json((run / "context.json").read_text()))
    metrics = validated_metrics(context, suite, records, events)
    by_id = {r["id"]: r for r in records}
    save_json(run / "metrics.json", metrics)
    save_json(run / "records.json", records)
    lines = [
        "# Direction 1: broader-context diagnostic pilot", "",
        f"Compiler method: `{method}`.", "",
        f"{len(suite.cases)} fixed, synthetic requests share one broad permission context. Annotations",
        "were authored by the coding assistant before the run, not independently human-reviewed.",
        "This is a developmental single-pass pilot, not a held-out benchmark or task-execution result.", "",
        "## Results", "", "| Measure | Count |", "|---|---:|",
        f"| Returned validated policies (including denials/questions) | {metrics['outcomes'].get('accepted', 0)}/{metrics['total_cases']} |",
        f"| Correct status | {metrics['status_correct']}/{metrics['total_cases']} |",
        f"| Correct task scope | {metrics['scope_correct']}/{metrics['total_cases']} |",
        f"| Full annotation match (also labels/question-topic screen) | {metrics['full_annotation_match']}/{metrics['total_cases']} |",
        f"| Returned policies with task overgrants | {metrics['cases_with_overgrants']}/{metrics['total_cases']} |",
        f"| Cases missing required grants | {metrics['cases_with_undergrants']}/{metrics['total_cases']} |",
        f"| Returned policies with evidence-label errors | {metrics['cases_with_basis_errors']}/{metrics['total_cases']} |", "",
        "Invalid/API-failed cases remain in all-case denominators and cannot score as successes.",
        "A rejected candidate is not an intentional denial. Every accepted policy passed",
        "the deterministic validator; that ceiling-compliance result follows from the pipeline,",
        "and does not establish correct task interpretation or adversarial robustness.", "",
        "## Per-case results", "",
        "| Case | Expected | Returned/outcome | Scope correct | Details |", "|---|---|---|---|---|",
    ]
    for case in suite.cases:
        record = by_id[case.id]
        a = record["assessment"]
        details = (["extra " + ", ".join(a["overgrants"])] if a["overgrants"] else [])
        if a["undergrants"]:
            details.append("missing " + ", ".join(a["undergrants"]))
        if a["basis_errors"]:
            details.append("basis " + ", ".join(a["basis_errors"]))
        if a["clarification_topics_missing"]:
            details.append("question topics " + ", ".join(a["clarification_topics_missing"]))
        if record.get("error"):
            details.append(record["error"])
        observed = record["policy"]["status"] if record["policy"] else record["outcome"]
        lines.append(f"| {case.id} | {case.expected.status} | {observed} | {'yes' if a['scope_correct'] else 'no'} | {'; '.join(details) or '—'} |")
    lines += [
        "", "## Definitions and limits", "",
        "Task scope requires the expected status, every required read, no read beyond",
        "the required/optional set, exactly the annotated operations, resource/recipient",
        "sets, and call counts. Optional directory access accommodates legitimate identity",
        "resolution. Other project context is not presumed necessary without a request",
        "dependency. Action argument/expiration limits are checked by the validator.", "",
        "Evidence labels are assessed separately: explicitly named source reads and",
        "requested operations are explicit; optional directory reads are necessary_inference.",
        "Clarification topic coverage uses documented keyword matching on field/question",
        "text, not a semantic judge. It cannot establish that a question is useful or sufficient.", "",
        "Only the request and unchanged trusted context reach the compiler. Expected",
        "policies never enter its prompt. No gold policy is used to repair candidate output.",
        "The compiler/annotations are frozen for the run, with hashes in metadata.json.",
        "There are no model retries or tuning after observing case outcomes.", "",
        "Failures retain candidate text and usage in api.jsonl; exceptions contain only",
        "sanitized messages/types. No credentials, authentication headers, .env contents,",
        "real memory contents, or live tool outputs are recorded. No tools were executed.", "",
        "## Calls, usage, and latency", "",
        f"API calls: {metrics['api_calls']}; requested models: `{json.dumps(metrics['requested_models'])}`;",
        f"returned models: `{json.dumps(metrics['returned_models'])}`.",
        f"Returned usage: `{json.dumps(metrics['usage'])}`. Sum of API wall time: {metrics['api_wall_seconds']} seconds.",
        "Usage is reported by the provider; failed calls may lack usage. No price estimate is inferred.",
        "Budget: at most 20 calls, SDK retries disabled, 2,048 output tokens/call,",
        "32,000 conservative input-token bound/call, 60-second timeout/call.", "",
        "## Reproduce", "", "```bash",
        f".venv/bin/python -m policy_compiler.evaluate --method {method} \\",
        f"  --context {display_path(run / 'context.json')} \\",
        f"  --cases {display_path(run / 'cases.json')}",
        "```", "", "This creates a new run and makes new API calls. To rebuild this report offline:", "", "```bash",
        f".venv/bin/python -m policy_compiler.evaluate --report-only {display_path(run)}",
        "```", "", "See context.json, cases.json, metadata.json, records.json, and api.jsonl in this run.",
    ]
    (run / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics


def run_evaluation(context_path, cases_path, output_root, model=None, method="original"):
    if method not in ("original", "revised"):
        raise ValueError("Unknown compiler method.")
    context, suite = read_suite(context_path, cases_path)
    # Import only after all annotations have been checked, before any API request.
    import openrouter_client as backend
    now = datetime.now(timezone.utc)
    run = Path(output_root).resolve() / now.strftime("%Y%m%dT%H%M%S%fZ")
    run.mkdir(parents=True, exist_ok=False)
    save_json(run / "context.json", context.model_dump())
    save_json(run / "cases.json", suite.model_dump())
    sources = ["policy_compiler/compiler.py", "policy_compiler/schema.py",
               "policy_compiler/validator.py", "policy_compiler/evaluate.py", "openrouter_client.py",
               "policy_compiler/__init__.py", "policy_compiler/effective.py", "policy_compiler/task_compiler.py",
               "policy_compiler/references.py"]
    for source in sources:
        snapshot = run / "source_snapshot" / source
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes((ROOT / source).read_bytes())
    metadata = dict(
        started_utc=now.isoformat(), display_timezone="Asia/Baku", requested_model=backend.get_model(model),
        annotation_provenance=suite.annotation_provenance, expected_cases=len(suite.cases),
        input_hashes={"context": digest(run / "context.json"), "cases": digest(run / "cases.json")},
        source_hashes={p: digest(ROOT / p) for p in sources},
        budget=dict(max_calls=len(suite.cases), max_output_tokens=MAX_OUTPUT_TOKENS,
                    max_input_token_bound=MAX_INPUT_TOKEN_BOUND,
                    conservative_total_token_bound=len(suite.cases) * (MAX_OUTPUT_TOKENS + MAX_INPUT_TOKEN_BOUND)),
        method=method, temperature=0, sdk_retries=0, timeout_seconds=60, seed=None, executed=False,
        python_version=sys.version.split()[0], packages={
            name: importlib.metadata.version(name) for name in ("openai", "pydantic", "python-dotenv", "httpx")},
    )
    save_json(run / "metadata.json", metadata)
    records, events = [], []
    print(f"Run: {display_path(run)}", flush=True)
    print(f"Budget: {len(suite.cases)} calls, no retries, max output {MAX_OUTPUT_TOKENS} tokens/call", flush=True)
    for case in suite.cases:
        def completion(messages, model=None, **kwargs):
            if len(events) >= len(suite.cases):
                raise CompilationError("Evaluation call budget exhausted.")
            bound = sum(len(m["content"].encode("utf-8")) + 32 for m in messages)
            if bound > MAX_INPUT_TOKEN_BOUND:
                raise CompilationError("Evaluation input token bound exceeded.")
            event = dict(case_id=case.id, requested_model=backend.get_model(model),
                         input_token_bound=bound, max_output_tokens=MAX_OUTPUT_TOKENS,
                         messages=messages, status="pending")
            started = time.monotonic()
            try:
                result = backend.get_client().with_options(max_retries=0, timeout=60).chat.completions.create(
                    model=event["requested_model"], messages=messages,
                    max_tokens=MAX_OUTPUT_TOKENS, **kwargs)
                event.update(status="ok", returned_model=result.model,
                             usage=result.usage.model_dump() if result.usage else None,
                             choices=[c.model_dump() for c in result.choices])
                return result
            except Exception as exc:
                event.update(status="api_error", error_type=type(exc).__name__)
                raise CompilationError("OpenRouter call failed; no policy was accepted.") from None
            finally:
                event["wall_seconds"] = round(time.monotonic() - started, 3)
                events.append(event)
                with (run / "api.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        started = time.monotonic()
        record = dict(id=case.id, category=case.category, request=case.request, policy=None)
        before = len(events)
        try:
            compiler = compile_task_policy if method == "revised" else compile_policy
            policy = compiler(case.request, **context.model_dump(), model=model, completion=completion)
            record.update(outcome="accepted", policy=policy.model_dump())
        except PolicyValidationError as exc:
            record.update(outcome="validation_error", error=str(exc))
        except CompilationError as exc:
            status = events[-1]["status"] if len(events) > before else "budget_error"
            record.update(outcome="api_error" if status == "api_error" else "compilation_error", error=str(exc))
        record["wall_seconds"] = round(time.monotonic() - started, 3)
        record["assessment"] = assess(case, TaskPolicy.model_validate(record["policy"]) if record["policy"] else None)
        records.append(record)
        with (run / "records.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        observed = record["policy"]["status"] if record["policy"] else record["outcome"]
        print(f"{len(records):02d}/{len(suite.cases)} {case.id}: {observed}; scope_correct={record['assessment']['scope_correct']}", flush=True)
    metadata.update(finished_utc=datetime.now(timezone.utc).isoformat(),
                    sources_unchanged=all(digest(ROOT / p) == h for p, h in metadata["source_hashes"].items()))
    save_json(run / "metadata.json", metadata)
    metrics = report(run, suite, records, events)
    print(f"Report: {display_path(run / 'REPORT.md')}", flush=True)
    print(f"Correct task scope: {metrics['scope_correct']}/{metrics['total_cases']}", flush=True)
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, default=FIXTURES / "context.json")
    parser.add_argument("--cases", type=Path, default=FIXTURES / "cases.json")
    parser.add_argument("--output-root", type=Path, default=ROOT / "results/policy_compiler/runs")
    parser.add_argument("--model", help="Optional override of configured OpenRouter model")
    parser.add_argument("--method", choices=["original", "revised"], default="original")
    parser.add_argument("--report-only", type=Path, help="Recompute a saved run offline; no API calls")
    args = parser.parse_args(argv)
    if args.report_only:
        run = args.report_only.resolve()
        metadata = load_json((run / "metadata.json").read_text())
        for name in ("context", "cases"):
            if digest(run / (name + ".json")) != metadata["input_hashes"][name]:
                raise ValueError("Saved evaluation annotations/context have changed.")
        _, suite = read_suite(run / "context.json", run / "cases.json")
        records = [load_json(line) for line in (run / "records.jsonl").read_text().splitlines()]
        events = [load_json(line) for line in (run / "api.jsonl").read_text().splitlines()]
        report(run, suite, records, events)
    else:
        run_evaluation(args.context, args.cases, args.output_root, args.model, args.method)


if __name__ == "__main__":
    main()
