"""Report actual telemetry in recorded calls: no live model execution required."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from voicebot.latency_report import markdown, summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('calls', type=Path, nargs='?', default=Path('logs/calls.jsonl'))
    parser.add_argument('--format', choices=('json', 'markdown'), default='markdown')
    args = parser.parse_args()
    try:
        with args.calls.open() as file:
            calls = [json.loads(line) for line in file if line.strip()]
        result = summarize(calls)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, allow_nan=False) if args.format == 'json' else markdown(result), end='\n')


if __name__ == '__main__':
    main()
