# Scenario C — Ambiguous Requirement: Clarification Process

## The original requirement

> "Regulators need to audit access to client account data."

This statement was deliberately under-specified. It does not say:

- What "access" means — reads? writes? failed attempts?
- What "client account data" means — which resource types?
- What regulators need to *do* with the data — query interactively, download a file, verify integrity?
- How regulators are identified — a role? an API key? a separate portal?
- What time window they need — last 90 days? all-time?
- Whether the audit trail must be cryptographically verifiable or just machine-readable

---

## How I would clarify this in practice

Before writing a line of code, I would schedule a 30-minute call with the compliance lead and/or the product manager representing the regulator's needs. The questions I would ask:

| # | Question | Why it matters |
|---|---|---|
| 1 | What specific event types need to be auditable? (`account.read`, `account.write`, `account.login_failed`, …) | Determines which events must be logged at all |
| 2 | Which resource types map to "client account data"? | Determines the filter key for export |
| 3 | Do regulators need to self-serve (portal/API) or does compliance staff pull a report on their behalf? | Changes whether we need auth/roles on the export endpoint |
| 4 | Must the export be cryptographically verifiable, or is human-readable sufficient? | Determines whether chain_hash walkthrough needs to be part of the export |
| 5 | What file format do regulators' systems ingest? | CSV vs. JSON vs. a regulator-specific format |
| 6 | Is there a mandated retention window? (e.g., 7 years under SEC Rule 17a-4) | Informs the archival / retention policy |
| 7 | Are there PII fields in the payload that must be redactable before handing to regulators? | Determines whether field-level redaction is a prerequisite |

---

## Assumptions made in this implementation

Since no stakeholder was available to answer the above questions during this assignment, I made the following explicit assumptions:

| Assumption | Rationale |
|---|---|
| "Client account data" maps to `resource_type` in the audit entry | `resource_type` is the natural key for categorising what kind of object an event touches |
| Regulators submit `resource_type=account` (or similar) to filter the export | Aligns with the existing query model; no schema change required |
| Compliance staff (not regulators directly) pull exports via the API | Avoids designing a separate auth/portal for this assignment's scope |
| The existing hash chain satisfies "cryptographic verifiability" | SHA-256 chain walk via `GET /audit/verify` proves no record was added or removed |
| Retention / time-window filtering is handled by the `from_time` / `to_time` query params | Already implemented in `GET /audit/events` |
| PII redaction is handled separately via `PATCH /audit/events/{id}/redact` before export | Redaction is chain-safe (does not break `entry_hash` or `chain_hash`) |

---

## What was implemented

Based on the assumptions above, the following capabilities together address the regulator audit requirement:

1. **Filtered list** — `GET /audit/events?resource_type=account&from_time=…&to_time=…`  
   Returns a paginated list of all events touching account resources in a date window.

2. **Filtered export** — `GET /audit/export?format=csv&resource_type=account`  
   Streams a complete CSV (or JSON) export of all account-related events, suitable for handing to a regulator.  
   The same filters (`actor_id`, `resource_type`, `resource_id`) work on both the JSON and CSV export endpoints.

3. **Chain verification** — `GET /audit/verify`  
   Proves the exported data has not been tampered with by walking the SHA-256 chain end-to-end.

4. **Field-level redaction** — `PATCH /audit/events/{id}/redact`  
   Allows PII fields to be masked before export without invalidating the chain.

---

## What is explicitly out of scope

| Item | Reason |
|---|---|
| Regulator-facing self-serve portal | No auth/roles system specified; would require a separate auth layer |
| Regulator-specific export formats (e.g., FINRA CAT CSV) | No format spec provided; standard CSV/JSON is the safe default |
| Automated retention purge (e.g., delete after 7 years) | Retention policy not specified; archival (soft-delete) is the safe conservative choice |
| Event-type level access controls per regulator | Would require a roles/permissions model outside this assignment's scope |

---

## How this maps to the system

The `actor_id`, `resource_type`, and `resource_id` columns in every audit entry are precisely the filtering axes that let a compliance officer produce a self-contained, verifiable bundle:

```
GET /audit/export?format=csv&resource_type=account&from_time=2026-01-01T00:00:00Z&to_time=2026-06-30T23:59:59Z
```

This returns every event that touched a resource of type `account` in H1 2026. The resulting file can be verified offline by walking the `chain_hash` column sequentially, confirming no records were inserted, deleted, or modified.
