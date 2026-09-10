# Routing audit and compact-model evaluation

Priority 6 first implementation slice: prompt correction, stricter output
validation, call context and a reproducible comparison harness. The production
model and default runtime profiles remain unchanged. Live model comparison and
promotion are pending.

## Audit findings and changes

1. The old prompt classified any request as off-topic. That contradicted its own
   categories for a human, callback, discount or stopping calls. The replacement
   distinguishes service intent from instructions aimed at controlling the
   classifier, and gives explicit multi-intent priorities.
2. The old parser accepted a label followed by arbitrary additional lines. It
   now requires the complete answer to be one recognized label, retaining minor
   formatting tolerance and one complete leading reasoning block for existing
   runtimes. Unterminated reasoning, trailing prose and multiple labels fail.
3. The model received only the turn number, language and transcript. It now
   receives the pending-question identifier and the engine's confirmed identity
   flag, without copying customer policy values into this context. JSON encoding
   prevents transcript delimiters from becoming additional context fields;
   semantic instruction resistance still requires evaluation.
4. Failure results now distinguish timeout, unavailable service and invalid
   output. The existing clarification/handoff path remains responsible for what
   happens next. Classification does not change identity or consent gates.

## Dataset and interpretation

`tests/eval/routing.jsonl` contains 66 authored review cases spanning all 19 labels,
English, Mandarin and Singlish, plus multi-intent requests and instruction
attacks. These are development/review cases, not an independently validated
held-out corpus and not recorded audio. Human review and a separate untouched
speaker/wording split are required before model promotion. No example utterances
from this file are embedded in the system prompt.

Accuracy counts every case, including failures and missing predictions. An
invalid response mapped to fallback `unclear` does not receive credit for a
case whose correct answer is `unclear`. Reports include per-language and
per-intent counts, confusion counts, individual failures, statuses and available
wall-clock timings. Missing latency stays missing. Timeout/error durations
remain in the overall latency distribution rather than disappearing from it.

## Run a comparison

Export prompts without answer labels:

```sh
python scripts/eval_routing.py export --output /tmp/routing-prompts.json
```

For an already-running local OpenAI-compatible chat-completions service, pass
its server root (without `/v1`) and exact served model identifier:

```sh
python scripts/eval_routing.py live \
  --base-url http://127.0.0.1:8000 --model MODEL_ID \
  --revision MODEL_REVISION --runtime RUNTIME_VERSION \
  --quantization QUANTIZATION --state warm \
  --output /tmp/routing-baseline.json
```

The harness sends serial requests with temperature zero, the production router's
8-token output cap, and a 1,500 ms total deadline per request. It performs no
warmup or retries; `--state` describes operator-controlled conditions. Only the
model service receives requests; the harness loads no local ASR, TTS or LLM
weights. It does not configure the service's chat template or reasoning mode;
record those deployment settings and use the same settings for comparisons.
An in-process MLX model can instead be evaluated using the exported requests.

For external inference, retain `dataset_sha256` and `system_prompt_sha256` from
the export and add a `predictions` array to the JSON document:

```json
{"id":"price-en","reply":"price","status":"ok","wall_ms":123.4}
```

Each array element follows that shape. Accepted failure statuses are `timeout`,
`unavailable` and `invalid_output`; `wall_ms` is optional and must be genuinely
measured. Then score the saved document:

```sh
python scripts/eval_routing.py score --predictions /tmp/predictions.json \
  --model MODEL_ID --revision MODEL_REVISION --runtime RUNTIME_VERSION \
  --quantization QUANTIZATION --state warm --output /tmp/routing-score.json
```

Dataset and prompt hashes must match. Model/runtime/revision and externally
supplied timings remain operator-declared provenance. Unknown settings remain
`unknown`; a report does not certify which weights the service actually loaded.
Live reports retain raw predictions and hashes and can be rescored directly.

Run the baseline and candidate separately with the same dataset hash, prompt
hash, timeout, reasoning settings and sampling parameters. Compare accuracy and
critical failures before latency. Measure peak RAM/GPU memory and full voice
response latency on the target Mac or GPU host separately: this harness cannot
infer those measurements from an HTTP response. Keep the prior model as default
until the [promotion gates](compact-model-routing-plan.md) pass.

## Verification for this slice

The full Python regression run passed 787 tests with one skipped and existing
dependency/API deprecation warnings. The final focused routing run passed 60
tests, including three additional context, provenance and HTTP-deadline checks.
Keyword-layer transcript replay met 31/31 expectations across 270 caller turns.
The 66-case prompt export completed successfully. HTTP behavior was verified
with an in-process mock transport; no live model accuracy, GPU memory or voice
latency result is claimed. The review dataset still requires human validation
and a separate held-out set before a model selection decision.
