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

## Compare baseline and candidate reports

After running each model separately using the same exported cases and prompt:

```sh
python scripts/compare_routing.py /tmp/routing-baseline.json \
  /tmp/routing-candidate.json --output /tmp/routing-comparison.json
```

This command recomputes results from the reports' raw `predictions`; it ignores
supplied accuracy and latency summaries. Mismatched dataset/prompt hashes,
prompt versions, output caps or timeout budgets are rejected. Missing model,
revision, runtime or quantization information blocks a passing result. Matching
operator-declared cold/warm conditions and complete measured timings are
required for the latency comparison. These declarations are not independently
verified by the command.

The provisional routing checks require:

- Overall accuracy of at least 95%, with no overall or per-language regression.
- Every critical category represented and no failed stop-call, human-handoff,
  advice or email-change case.
- Failure rate at most 2%, including missing/invalid/unavailable/timed-out
  outputs, and no increase in timeout count.
- Neither p50 nor p95 latency worse, and at least one strictly better.

Use `--min-accuracy` and `--max-failure-rate` only for explicitly agreed
experiment criteria; the output records those settings. The defaults are
provisional, not production approval. Paired `regression_ids` and `fixed_ids`
expose changed behavior even if aggregate accuracy is identical. Negative
`latency_delta_ms` means the candidate was faster in the supplied measurements.

Exit status 0 means these routing-only checks passed; status 1 means blocked;
status 2 means invalid input or incomparable reports. `promotion_eligible`
remains false: independent reviewed held-out data, deterministic action/consent
regressions, verification of deployed weights/settings, measured total memory,
and sustained end-to-end voice tests must still be reviewed separately.
The current 66 cases are explicitly labelled `review`, not held-out validation.

The comparator and existing evaluation harness passed 23 focused tests, covering
fabricated summaries, mismatched conditions, missing predictions/timing,
critical errors, invalid thresholds and CLI exit behavior. Passing fixture
reports are tests of comparison logic, not measured model results.

At this checkpoint neither the configured `127.0.0.1:8000` model service nor
`127.0.0.1:11434` was reachable from the development workspace. Run the live
commands on the Mac/GPU host where the service is running, then supply both JSON
reports for comparison. No live accuracy, speedup or memory improvement has
been established, and the default model is unchanged.
