# Context compaction PR baseline v1

This baseline was generated from clean commit `3559223b5fd7af9be5464e04ae23ee1ab2c55113` with:

```bash
python -m evals.run_context_compaction_eval \
  --iterations 10 \
  --output evals/baselines/context_compaction_v1/results.jsonl \
  --manifest evals/baselines/context_compaction_v1/manifest.json
```

It contains six deterministic synthetic cases. The baseline deliberately preserves the known `raw_tool_dependency` counterexample, so compacted correctness and answer consistency are `5/6` rather than claiming perfect retention.

The CI policy is [context_compaction_pr_v1.json](../../policies/context_compaction_pr_v1.json). It checks correctness and size/token-proxy reduction using both absolute limits and allowed regression deltas. Local latency measurements remain in result rows for diagnosis but are excluded from the blocking policy because they are machine-dependent.

Do not overwrite this directory to move a threshold. Create a new baseline/policy version, record why the expected behavior changed, and keep the old pair long enough to audit the transition.
