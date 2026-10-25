"""Run with python -m policy_compiler; offline mode only validates saved output."""

import argparse
import sys
from pathlib import Path

from .compiler import CompilationError, compile_policy
from .task_compiler import compile_task_policy
from .grounded import compile_grounded_policy
from .validator import PolicyValidationError, load_json, validate_context, validate_policy


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compile a task policy without executing tools.")
    parser.add_argument("--request", required=True, help="Original authenticated task request")
    parser.add_argument("--context", required=True, type=Path, help="Trusted structured context JSON")
    parser.add_argument("--model", help="Optional override of OPENROUTER_MODEL")
    parser.add_argument("--method", choices=["revised", "original", "grounded"], default="revised",
                        help="Use revised task-needs inference (default) or original direct policy inference")
    parser.add_argument("--validate-response", type=Path,
                        help="Validate saved policy JSON offline; does not compile a new request")
    parser.add_argument("--resource-catalog", type=Path, help="Trusted aliases/dependencies for --method grounded")
    args = parser.parse_args(argv)
    try:
        context = validate_context(load_json(args.context.read_text(encoding="utf-8")))
        if args.validate_response:
            policy = validate_policy(args.validate_response.read_text(encoding="utf-8"), args.request, context)
        else:
            if args.method == "grounded":
                if args.resource_catalog is None:
                    raise PolicyValidationError("Grounded compilation requires --resource-catalog.")
                policy = compile_grounded_policy(args.request, **context.model_dump(), model=args.model,
                    resource_catalog=load_json(args.resource_catalog.read_text(encoding="utf-8")))
            else:
                compiler = compile_task_policy if args.method == "revised" else compile_policy
                policy = compiler(args.request, **context.model_dump(), model=args.model)
        print(policy.model_dump_json(indent=2))
        return {"ready": 0, "clarification_required": 2, "denied": 3}[policy.status]
    except (PolicyValidationError, CompilationError) as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, UnicodeError, RecursionError):
        print("Could not read a valid UTF-8 JSON input file.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
