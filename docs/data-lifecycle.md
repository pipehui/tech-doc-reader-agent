# Data Lifecycle, Retention, Backup, and Deletion Policy

## Status and scope

This document defines the current lifecycle policy for runtime data owned or referenced by Tech Doc Reader Agent. It is an
engineering policy, not a claim of legal compliance. The project does not currently expose a GDPR-like export/delete API and must
not add one until the prerequisites in this document are implemented and tested.

The safe default is deliberately conservative:

- pending approval data uses its existing Redis TTL;
- usage counters overwrite their current state;
- local diagnostic traces are opt-in and retain only the latest configured request files;
- all other durable records are retained until an explicit, authenticated operation exists;
- generation, processed-command, legacy-source, and migration-backup pruning is disabled;
- current manifests and their referenced generations must never be deleted independently.

No arbitrary “30/90 day” period is introduced without a product/legal requirement and an enforceable deletion/backup design.

## Data classification and current retention

| Store | Data | Scope | Current retention | Current deletion | Backup/replica notes |
|---|---|---|---|---|---|
| Redis guardrail approval | Original medium-risk input, findings, tenant/session | Tenant + session | `GUARDRAIL_APPROVAL_TTL_SECONDS`, default 900 seconds | Redis expiry or atomic `GETDEL` on resolve | Do not include pending approval keys in long-lived application backups. Redis AOF may retain historical commands until rewrite. |
| Redis LangGraph checkpoint | Full messages, tool calls/results, plan and agent state | Tenant + session thread id | No application TTL is configured; effectively retained by Redis deployment policy | No authenticated application delete use case exists | Redis AOF/backup policy is external. Restoring checkpoints without matching application/prompt/tool versions may not be replay-safe. |
| LearningState current snapshot | Learning records, memory fragments, processed command outcomes | Multiple tenants in one snapshot | Retain until explicit tenant or repository deletion | No delete port/API exists | Back up `current.json` together with its referenced generation. Never back up or restore one file from a generation in isolation. |
| LearningState non-current generations | Previous published generations and possible interrupted publication artifacts | Repository-wide | Retain; automatic GC disabled | None | Current metadata cannot distinguish previously-published history from every possible orphan. Inventory is read-only. |
| User Profile | Stable preferences, topic lists, evidence and update time | Tenant | Latest version retained until explicit tenant deletion | No delete port/API exists | Atomic envelope file. Migration backup/legacy source can contain older copies. |
| FAISS current snapshot | Shared documents, chunks, index | Shared knowledge base, not tenant-private today | Retain until explicit administrator replacement | No delete/GC API exists | Back up manifest and the complete referenced generation. Document ACL/deletion is separate from user-data deletion. |
| FAISS non-current generations | Previous index generations and possible interrupted artifacts | Repository-wide | Retain; automatic GC disabled | None | Same classification limitation as LearningState generations. |
| Migration backups and legacy sources | Copies of learning/memory/profile data | Mixed | Operator-controlled; retain through migration verification and one tested rollback window | No automatic deletion | May contain personal data even after current state changes. Backup expiry must be part of a future operator policy. |
| Web search usage state | Date and Tavily call count | Deployment | Current day/counter is overwritten | Daily state replacement | Does not store search query/results in the current adapter. |
| Local diagnostic traces | Full request, prompt, model/tool input-output, span hierarchy, and raw exception stack when content capture is enabled | Deployment-local request trace | Latest `LOCAL_TRACE_RETENTION_COUNT` completed requests, default 100 | Oldest completed file is removed after finalization; active files are recovered as abandoned after restart | Stored under `${DATA_PATH}/traces`; excluded from Git and Docker build context. Contains raw sensitive content and is not included in a user-facing export/delete API. |
| Structured logs / Langfuse | Redacted events, pseudonymous tenant metadata, traces | External sink | Controlled by log platform/Langfuse configuration | External | Shared redaction is mandatory before export. External retention must be documented per deployment. |
| Frontend browser storage | Session directory, transcript/context/preferences | Browser/device | Until user/browser clears or UI delete/reset runs | Client-side repository delete/reset | Not covered by backend backups. A backend delete API cannot erase another device’s local storage. |

## Ownership and addressability

Deletion can only be correct when every record can be associated with its owner and storage scope.

- Profiles are physically keyed by encoded `(user_id, namespace)`.
- Learning records and memory fragments carry tenant fields inside the shared snapshot.
- Approval and checkpoint keys include the tenant thread prefix.
- FAISS documents are explicitly shared today and are not included in tenant-data deletion.
- New processed-command entries carry a deterministic `owner_key = SHA-256(user_id, namespace)` so a future transaction can select
  entries for a tenant without storing a second raw tenant copy in every command outcome.

`owner_key` is a routing/index key, not anonymization: tenant identifiers often have low entropy and a plain digest can be guessed. It
must not be exposed as a public privacy guarantee. Legacy processed-command entries have no owner key because their hashed idempotency
identity cannot be reversed; they are retention-protected and cannot be automatically removed by tenant until an explicit migration or
full-snapshot policy resolves them.

## Processed command retention

Processed command outcomes prevent LangGraph checkpoint replay from repeating sensitive learning writes. Removing one too early can
increment `reviewtimes` again or duplicate a memory after resume.

Current policy:

1. Processed outcomes are retained without automatic expiry.
2. A finite retention period cannot be configured until checkpoint retention establishes a maximum replay window.
3. Any future pruning must use `completed_at`, require an owner key for tenant-scoped deletion, and retain evidence longer than the
   maximum checkpoint/session replay period plus deployment rollback overlap.
4. Legacy outcomes without owner key are not eligible for automatic tenant pruning.
5. Pruning and business-data changes must publish one LearningState transaction; a separate cache/file delete is not allowed.

## Generation retention and inventory

`GenerationStore.inventory()` is the only lifecycle operation added at this stage. It reports:

- whether a current manifest exists;
- the current generation id and whether its directory is present;
- all syntactically valid generation directories;
- non-current generation ids;
- unknown entries.

“Non-current” intentionally does not mean “safe orphan.” With the current manifest format, a directory can be a prior successful
generation, a publication-started candidate whose manifest replacement failed, or an operator copy. Automatic generation GC remains
disabled until all of the following exist:

1. a single-writer/process-lock rule that deletion and publication both honor;
2. per-generation publication/history metadata sufficient to classify candidates;
3. a minimum retained-version/age policy based on real recovery requirements;
4. verified backup and restore procedures;
5. dry-run inventory and current-generation protection;
6. fault/concurrency tests proving a newly published current generation cannot be removed.

The only recursive cleanup currently allowed is an unpublished `GenerationDraft` before manifest publication starts. Once publication
starts, cleanup retains the candidate because an atomic replace may have succeeded immediately before interruption.

## Migration backup policy

The explicit legacy migration command always backs up every source before apply and never deletes legacy sources. Backup files preserve
their path relative to `DATA_PATH`; an existing destination is reused only when its hash matches.

Before deleting a migration backup or legacy source, an operator must:

1. stop writers or enforce the future process lock;
2. record the migration report and commit/application version;
3. load and validate the new LearningState/Profile data through repository contracts;
4. perform at least one restore rehearsal into an isolated data directory;
5. confirm the rollback window has ended;
6. account for copies in Redis AOF, filesystem snapshots, host backups, and external observability systems.

There is no automatic backup retention timer today. This is intentional until deployment-specific RPO/RTO and storage ownership are
defined.

## Restore procedure

Restores are repository-level, not individual-file edits:

1. Stop all application writers.
2. Preserve the failed/current data directory separately before overwriting it.
3. Restore Redis with the deployment’s supported AOF/RDB process if checkpoints are required.
4. Restore a generation repository as `current.json` plus the complete referenced generation directory.
5. Restore Profile envelopes at their tenant paths; do not copy a root legacy profile over an existing tenant-scoped profile.
6. Run repository contract tests or an equivalent isolated verification and application readiness checks.
7. Verify manifest counts/index shape and sample tenant isolation before restarting writers.
8. Record the restored application commit, prompt/model identity where available, backup identifier, operator, and verification result.

No RPO/RTO is currently promised. Those values must be set from deployment requirements and tested recovery time, not inferred from the
existence of generation directories.

## Requirements before a user export/delete API

A GDPR-like API remains blocked until these conditions are met:

1. Trusted authentication provides the subject; namespace access is authorized independently of request body/query values.
2. Repository ports define typed inventory/export/delete operations with dry-run and idempotency.
3. LearningState deletion atomically filters records, memories, and owned processed outcomes in one new generation.
4. Redis checkpoint and pending-approval keys can be enumerated/deleted for the authorized tenant without wildcard cross-tenant access.
5. Legacy processed outcomes without owner key have an explicit migration/full-snapshot rule.
6. Backup, AOF, local traces, logs, Langfuse, and browser-storage limitations are disclosed; “delete complete” must not claim immediate physical
   erasure of unmanaged copies.
7. Deletion produces a minimal audit event that does not copy the deleted content.
8. Legal/security hold semantics exist if the deployment requires them; held data must block both primary and backup expiry.
9. Concurrency tests prove deletion cannot race a write/resume and resurrect tenant data.
10. Shared FAISS documents are excluded unless document ownership/ACL is introduced separately.

Until these prerequisites are implemented, deletion remains an operator-controlled maintenance action, not a public endpoint.

## Enforced invariants

Automated tests currently enforce that:

- pending approval TTL is positive and passed to Redis `SET EX`;
- processed command owner keys, when present, are valid 64-character digests;
- legacy outcomes without owner keys remain readable;
- generation inventory never deletes data;
- persistence adapters expose no public `delete/prune/purge/gc/retention` method before the policy prerequisites are implemented;
- migration dry-run is write-free, apply backs up sources, and repeated apply does not create new generations;
- current generation publication and repository contract invariants remain green.
