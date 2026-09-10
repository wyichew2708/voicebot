"""Export blind prompts, score saved predictions, or evaluate an existing local LLM service."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from voicebot.call import router
from voicebot.routing_eval import load_cases, prompt, score


async def live(cases, args):
    import httpx
    rows = []
    # Serial requests: this is a routing comparison, not a load test. No ASR/TTS
    # models are initialized, and no retry hides a failure from the report.
    async with httpx.AsyncClient(timeout=args.timeout_ms / 1000) as client:
        for c in cases:
            start = time.perf_counter()
            row = {'id':c['id'], 'status':'ok'}
            try:
                async with asyncio.timeout(args.timeout_ms / 1000):
                    response = await client.post(args.base_url.rstrip('/') + '/v1/chat/completions',
                        json={'model':args.model, 'temperature':0, 'max_tokens':router.MAX_TOKENS,
                              'messages':[{'role':'system','content':router.system_prompt()},
                                          {'role':'user','content':prompt(c)}]})
                    response.raise_for_status()
                    row['reply'] = response.json()['choices'][0]['message']['content']
            except (httpx.TimeoutException, TimeoutError):
                row['status'] = 'timeout'
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                row['status'] = 'unavailable'
            row['wall_ms'] = round((time.perf_counter()-start)*1000, 3)
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('export', 'score', 'live'))
    parser.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[1]/'tests/eval/routing.jsonl')
    parser.add_argument('--predictions', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model')
    parser.add_argument('--revision', default='unknown')
    parser.add_argument('--runtime', default='unknown')
    parser.add_argument('--quantization', default='unknown')
    parser.add_argument('--state', choices=('cold','warm','unknown'), default='unknown')
    parser.add_argument('--base-url')
    parser.add_argument('--timeout-ms', type=int, default=1500)
    args = parser.parse_args()
    if args.timeout_ms <= 0:
        parser.error('--timeout-ms must be positive')
    if args.mode == 'live' and (not args.base_url or not args.model):
        parser.error('live requires --base-url and --model')
    if args.mode == 'score' and (not args.predictions or not args.model):
        parser.error('score requires --predictions and --model')
    try:
        cases, digest = load_cases(args.cases)
        metadata = {'dataset_sha256':digest, 'prompt_version':router.PROMPT_VERSION,
                    'system_prompt_sha256':hashlib.sha256(router.system_prompt().encode()).hexdigest(),
                    'model':args.model, 'revision':args.revision, 'runtime':args.runtime,
                    'quantization':args.quantization, 'declared_state':args.state,
                    'mode':args.mode, 'max_tokens':router.MAX_TOKENS, 'timeout_ms':args.timeout_ms}
        if args.mode == 'export':
            result = {**metadata, 'requests':[{'id':c['id'], 'system':router.system_prompt(),
                                             'user':prompt(c)} for c in cases]}
        else:
            if args.mode == 'live':
                predictions = asyncio.run(live(cases, args))
            else:
                saved = json.loads(args.predictions.read_text())
                for key in ('dataset_sha256', 'system_prompt_sha256'):
                    if saved.get(key) != metadata[key]:
                        raise ValueError(f'Prediction provenance mismatch: {key}')
                predictions = saved['predictions']
            result = {**metadata, **score(cases, predictions), 'predictions':predictions,
                      'limitations':'Routing only. Saved predictions use declared provenance. No measured GPU/RAM or audible latency; no model promotion implied.'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        parser.error(str(exc))
    print(f"Saved {args.mode} result for {len(cases)} cases to {args.output}")


if __name__ == '__main__':
    main()
