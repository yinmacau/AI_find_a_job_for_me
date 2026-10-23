# Converge Archive Index

## Decision

- value: 可执行
- type: reviewer-verdict
- event: [event](evidence/events/00000007-f7a71140-5837-4419-a95b-7af225377820.json)

## Integrity & Threat Boundary

- schema: `converge.archive 1.0`
- status: archived only when this directory is under the canonical done root and `check` reports valid
- guarantee: archive-time internal consistency, structural integrity, and traceable declared provenance
- does not guarantee: historical truth or resistance to a same-permission writer rewriting the whole archive and Git history

## Degradations

- model-provenance:configured
- permissions:acl-confidentiality-not-verified

## Revision Timeline

- current: r1

## Event Timeline

- 0001 `invocation-started` [d490f122-9162-4b94-8712-4f96ccc30791](evidence/events/00000001-d490f122-9162-4b94-8712-4f96ccc30791.json)
- 0002 `invocation-terminal` [70f1e039-961b-42e4-9559-7c8904128ab7](evidence/events/00000002-70f1e039-961b-42e4-9559-7c8904128ab7.json)
- 0003 `invocation-started` [2a7a5e2f-eb9d-4928-b199-416464391583](evidence/events/00000003-2a7a5e2f-eb9d-4928-b199-416464391583.json)
- 0004 `invocation-terminal` [60b1b9ed-6ce1-4277-9eb8-dc5ce3b20967](evidence/events/00000004-60b1b9ed-6ce1-4277-9eb8-dc5ce3b20967.json)
- 0005 `invocation-started` [27f122cd-7a30-4989-b93a-a6f654c27b9f](evidence/events/00000005-27f122cd-7a30-4989-b93a-a6f654c27b9f.json)
- 0006 `invocation-terminal` [0783fc8e-671e-4669-b9ad-599c30e8e03e](evidence/events/00000006-0783fc8e-671e-4669-b9ad-599c30e8e03e.json)
- 0007 `terminal-decision` [f7a71140-5837-4419-a95b-7af225377820](evidence/events/00000007-f7a71140-5837-4419-a95b-7af225377820.json)

## Model Provenance

- `d490f122-9162-4b94-8712-4f96ccc30791` invocation-started: requested=unavailable; resolved=unavailable; evidence=pending; source=pending; reason=none
- `70f1e039-961b-42e4-9559-7c8904128ab7` invocation-terminal: requested=unavailable; resolved=unavailable; evidence=configured; source=cli_argument; reason=backend-does-not-expose
- `2a7a5e2f-eb9d-4928-b199-416464391583` invocation-started: requested=unavailable; resolved=unavailable; evidence=pending; source=pending; reason=none
- `60b1b9ed-6ce1-4277-9eb8-dc5ce3b20967` invocation-terminal: requested=unavailable; resolved=unavailable; evidence=configured; source=cli_argument; reason=backend-does-not-expose
- `27f122cd-7a30-4989-b93a-a6f654c27b9f` invocation-started: requested=unavailable; resolved=unavailable; evidence=pending; source=pending; reason=none
- `0783fc8e-671e-4669-b9ad-599c30e8e03e` invocation-terminal: requested=unavailable; resolved=unavailable; evidence=configured; source=cli_argument; reason=backend-does-not-expose

## Artifact Provenance

- none

## Auxiliary Evidence (non-event-derived)

Imported and hash-verified, but not part of the event graph and not carrying any provenance
claim — see `EVIDENCE_RESERVED_SUBDIRS` in the Archive Contract for what *is* event-derived.

- none

## Residual Risks

- same-writer-rewrite-undetectable

## Next Reads

- [Final round](round-1.md)
- [Retrospective](retrospective.md)
- [Terminal decision evidence](evidence/events/00000007-f7a71140-5837-4419-a95b-7af225377820.json)
