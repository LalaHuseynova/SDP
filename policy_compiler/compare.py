"""Freeze fresh annotations, run original/revised methods, and score deterministic controls."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .baselines import ceiling_copy_policy, keyword_policy
from .evaluate import (
    ROOT, digest, display_path, read_suite, run_evaluation, save_json, summarize, validated_metrics,
)
from .validator import PolicyValidationError, load_json


def compare(context_path, cases_path, output_root, model=None):
    context, suite = read_suite(context_path, cases_path)
    development_requests = {c.request for c in read_suite(
        ROOT / "policy_compiler/evaluation/context.json", ROOT / "policy_compiler/evaluation/cases.json")[1].cases}
    if any(c.request in development_requests for c in suite.cases):
        raise ValueError("Fresh comparison requests must not duplicate development requests.")
    run = Path(output_root).resolve() / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run.mkdir(parents=True, exist_ok=False)
    save_json(run / "context.json", context.model_dump())
    save_json(run / "cases.json", suite.model_dump())
    sources = [p for p in (ROOT / "policy_compiler").glob("*.py")] + [ROOT / "openrouter_client.py"]
    frozen = {str(p.relative_to(ROOT)): digest(p) for p in sorted(sources)}
    for path in sources:
        snapshot = run / "source_snapshot" / path.relative_to(ROOT)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(path.read_bytes())
    manifest = dict(
        annotation_provenance=suite.annotation_provenance,
        source_hashes=frozen, input_hashes={name: digest(run / (name + ".json")) for name in ("context", "cases")},
        design="One pass per method, original then revised, same fixed context/cases; no tuning during comparison.",
        max_api_calls=2 * len(suite.cases), model=model, methods={}, executed=False,
    )
    save_json(run / "comparison.json", manifest)
    print(f"Fresh comparison: {display_path(run)}", flush=True)
    metrics = {}
    for name, method in (("ceiling_copy", ceiling_copy_policy), ("keywords", keyword_policy)):
        records = []
        for case in suite.cases:
            record = dict(id=case.id, category=case.category, request=case.request, policy=None)
            try:
                record.update(outcome="accepted", policy=method(case.request, context).model_dump())
            except PolicyValidationError as exc:
                record.update(outcome="validation_error", error=str(exc))
            records.append(record)
        metrics[name] = summarize(records, suite)
        save_json(run / (name + "_records.json"), records)
    for method in ("original", "revised"):
        if any(digest(ROOT / p) != h for p, h in frozen.items()):
            raise ValueError("Compiler/evaluation sources changed after comparison freeze.")
        child = run_evaluation(run / "context.json", run / "cases.json", run / method,
                               model=model, method=method)
        manifest["methods"][method] = str(child.relative_to(run))
        metrics[method] = load_json((child / "metrics.json").read_text())
        save_json(run / "comparison.json", manifest)
    manifest["sources_unchanged"] = all(digest(ROOT / p) == h for p, h in frozen.items())
    if not manifest["sources_unchanged"]:
        raise ValueError("Frozen sources changed during comparison.")
    manifest["metrics"] = metrics
    save_json(run / "comparison.json", manifest)
    write_report(run, manifest)
    print(f"Comparison report: {display_path(run / 'COMPARISON.md')}", flush=True)
    return run


def verified_comparison_metrics(run, manifest):
    """Read-only replay; never trust cached scores or compare historical hashes to current code."""
    context, suite = read_suite(run / "context.json", run / "cases.json")
    for name in ("context", "cases"):
        if digest(run / (name + ".json")) != manifest["input_hashes"][name]:
            raise ValueError("Saved comparison inputs changed.")
    for source, expected in manifest["source_hashes"].items():
        if digest(run / "source_snapshot" / source) != expected:
            raise ValueError("Saved comparison source snapshot changed.")
    metrics = {}
    for name in ("ceiling_copy", "keywords"):
        records = load_json((run / (name + "_records.json")).read_text())
        metrics[name] = validated_metrics(context, suite, records)
    for name in ("original", "revised"):
        child = (run / manifest["methods"][name]).resolve()
        if not child.is_relative_to(run.resolve()):
            raise ValueError("Saved method path is outside the comparison.")
        metadata = load_json((child / "metadata.json").read_text())
        if metadata.get("method", "original") != name:
            raise ValueError("Saved method metadata does not match the comparison.")
        for item in ("context", "cases"):
            expected = manifest["input_hashes"][item]
            if digest(child / (item + ".json")) != expected or metadata["input_hashes"][item] != expected:
                raise ValueError("Saved method inputs differ from the comparison.")
        for source, expected in metadata["source_hashes"].items():
            if (manifest["source_hashes"].get(source) != expected or
                    digest(child / "source_snapshot" / source) != expected):
                raise ValueError("Saved method source snapshot differs from the comparison.")
        records = [load_json(line) for line in (child / "records.jsonl").read_text().splitlines()]
        events = [load_json(line) for line in (child / "api.jsonl").read_text().splitlines()]
        metrics[name] = validated_metrics(context, suite, records, events)
        if metrics[name] != load_json((child / "metrics.json").read_text()):
            raise ValueError("Saved method metrics disagree with recomputed records.")
    if metrics != manifest["metrics"]:
        raise ValueError("Saved comparison metrics disagree with recomputed records.")
    return metrics


def write_report(run, manifest):
    verified_comparison_metrics(run, manifest)
    n = len(load_json((run / "cases.json").read_text())["cases"])
    lines = [
        "# Fresh-request comparison", "",
        f"{n} synthetic requests and a fixed context were annotated before the comparison.",
        "Sources and annotations were snapshotted before API calls. This runner does not verify",
        "that the method predates case authorship or that annotations received independent review.",
        "See the saved annotation provenance for authorship details. These cases exercise",
        "new entities, resource combinations, wording, and some additional composition/negation",
        "patterns. They are not a hidden independently held-out benchmark or new real-world domain.", "",
        "| Method | Valid results | Correct status | Correct scope | Full annotation match | Task overgrants | Missing grants |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("ceiling_copy", "keywords", "original", "revised"):
        m = manifest["metrics"][name]
        lines.append(f"| {name} | {m['outcomes'].get('accepted', 0)}/{n} | {m['status_correct']}/{n} | {m['scope_correct']}/{n} | {m['full_annotation_match']}/{n} | {m['cases_with_overgrants']}/{n} | {m['cases_with_undergrants']}/{n} |")
    lines += [
        "", "The original and revised methods each made one bounded call per case. Failures",
        "remain in the denominator. The two controls make no API calls. Ceiling copying",
        "copies every eligible read and broad operation scopes, binding only unique recipients",
        "actually named in the request and allocating counts under the aggregate limit.",
        "The keyword method uses exact resource/tool mentions, longest recipient aliases,",
        "a small English verb/count vocabulary, and the same trusted assembly as the revision.",
        "It is a meaningful check of whether LLM inference adds value beyond that processing.", "",
        "The revision supplies only effective ceilings to a task-needs model; Python supplies",
        "expiration, argument restrictions, source-read dependencies, and recipient-multiplied",
        "call counts. No model was trained, fine-tuned, or uncertainty-calibrated. The experiment",
        "changes prompt and representation together with deterministic assembly, so it cannot",
        "attribute improvements to any one component. No tools or memory were executed.", "",
        "Status, scope, optional-read handling, evidence-label conventions, and lexical",
        "clarification-topic checks use the pre-existing scorer. Question-topic matching",
        "does not establish useful clarification. These single-pass results support only",
        "a small diagnostic comparison; they do not establish adversarial robustness or",
        "paper-level generalization. Original runs precede revised runs; provider/time effects",
        "are uncontrolled. The original compiler source was preserved, not reconstructed.", "",
        "## Usage and reproducibility", "",
    ]
    for name in ("original", "revised"):
        m = manifest["metrics"][name]
        lines.append(f"- {name}: {m['api_calls']} calls; usage `{json.dumps(m['usage'])}`; API wall time {m['api_wall_seconds']} seconds. Details: `{manifest['methods'][name]}/REPORT.md`.")
    lines += [
        "", "Inputs, frozen source snapshots/hashes, deterministic-control outputs, and",
        "per-method response/usage logs are retained. No key/header/.env contents are logged.",
        "`comparison.json` includes the exact counts and source/input hashes.", "",
        "To run a new comparison (up to 40 new API calls):", "", "```bash",
        ".venv/bin/python -m policy_compiler.compare", "```", "",
        "To regenerate this summary offline:", "", "```bash",
        f".venv/bin/python -m policy_compiler.compare --report-only {display_path(run)}", "```",
    ]
    (run / "COMPARISON.md").write_text("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, default=ROOT / "policy_compiler/evaluation/fresh_context.json")
    parser.add_argument("--cases", type=Path, default=ROOT / "policy_compiler/evaluation/fresh_cases.json")
    parser.add_argument("--output-root", type=Path, default=ROOT / "results/policy_compiler/comparisons")
    parser.add_argument("--model")
    parser.add_argument("--report-only", type=Path)
    args = parser.parse_args(argv)
    if args.report_only:
        run = args.report_only.resolve()
        manifest = load_json((run / "comparison.json").read_text())
        write_report(run, manifest)
    else:
        compare(args.context, args.cases, args.output_root, args.model)


if __name__ == "__main__":
    main()
