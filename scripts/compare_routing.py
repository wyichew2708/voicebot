"""Compare two routing reports with identical prompts and evaluation cases."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from voicebot.routing_eval import compare, load_cases


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    p.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[1]/'tests/eval/routing.jsonl')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--min-accuracy', type=float, default=.95)
    p.add_argument('--max-failure-rate', type=float, default=.02)
    args = p.parse_args()
    try:
        cases, digest = load_cases(args.cases)
        result = compare(cases, digest, json.loads(args.baseline.read_text()),
                         json.loads(args.candidate.read_text()),
                         min_accuracy=args.min_accuracy, max_failure_rate=args.max_failure_rate)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        p.error(str(exc))
    print(f"Routing checks: {'PASS' if result['routing_checks_passed'] else 'BLOCKED'}. "
          f"Model promotion remains pending. Report: {args.output}")
    return 0 if result['routing_checks_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
