"""One small live request using the same backend as the agents.

Run explicitly; importing this file does not spend API credits.
"""
import argparse

from openrouter_client import call_llm, get_model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    print(f"Model: {get_model(args.model)}")
    reply = call_llm("Reply exactly with: OpenRouter works", model=args.model)
    if "OpenRouter works" not in reply:
        raise RuntimeError(f"Unexpected smoke-test reply: {reply!r}")
    print(reply)


if __name__ == "__main__":
    main()
