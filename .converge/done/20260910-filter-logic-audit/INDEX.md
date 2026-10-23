# Converge Archive Index

## Decision

- value: 阻断需修复
- type: reviewer-verdict
- event: [event](evidence/events/00000003-df17f400-ede7-41f6-b365-eb11875455ea.json)

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

- 0001 `invocation-started` [64099f50-9cd7-477f-84bd-a2eda9e24303](evidence/events/00000001-64099f50-9cd7-477f-84bd-a2eda9e24303.json)
- 0002 `invocation-terminal` [64586625-f8d9-4b77-b47f-7d31d4e64c40](evidence/events/00000002-64586625-f8d9-4b77-b47f-7d31d4e64c40.json)
- 0003 `terminal-decision` [df17f400-ede7-41f6-b365-eb11875455ea](evidence/events/00000003-df17f400-ede7-41f6-b365-eb11875455ea.json)

## Model Provenance

- `64099f50-9cd7-477f-84bd-a2eda9e24303` invocation-started: requested=unavailable; resolved=unavailable; evidence=pending; source=pending; reason=none
- `64586625-f8d9-4b77-b47f-7d31d4e64c40` invocation-terminal: requested=unavailable; resolved=unavailable; evidence=configured; source=cli_argument; reason=backend-does-not-expose

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
- [Terminal decision evidence](evidence/events/00000003-df17f400-ede7-41f6-b365-eb11875455ea.json)
