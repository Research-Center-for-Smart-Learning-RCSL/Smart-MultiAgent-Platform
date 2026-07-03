---
type: feature
status: draft
created: YYYY-MM-DD
requirements: []
---

# <Title>

## 1. Summary

One paragraph: what this feature does and for whom.

## 2. Goals and Non-goals

**Goals**
- ...

**Non-goals**
- Explicit exclusions that fence the implementation scope. Real exclusions, not filler.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | ... | ... | ... |

## 4. Current State

What the system does today in the affected area. Every claim cites `path:line`.
End-to-end data flow for the touched behavior if relevant.

## 5. Design

### Options considered

**Option A — <name>**: description. Trade-offs.

**Option B — <name>**: description. Trade-offs.

### Decision

Which option, why, and what was consciously given up. This section is the ADR of the
task — write it for the reader six months from now.

## 6. Detailed Changes

Per layer, respecting SoC boundaries:

- **Backend** — contexts touched, new/changed facade methods, services, repositories,
  tables. Migration required: yes/no (if yes, reversibility addressed in §10).
- **API contract** — new/changed endpoints, request/response models. `gen:api` rerun
  required: yes/no.
- **Frontend** — slices touched, new components/composables/stores, i18n keys.
- **Deploy/config** — env vars, Vault paths, compose changes.

## 7. NFR Checklist

Address each; "N/A" with a reason is a valid answer, silence is not.

- [ ] i18n — all user-facing strings through `$t()`
- [ ] Audit log — domain events that must be recorded
- [ ] Tenant isolation — org/project membership verified on every new endpoint
- [ ] Error handling UX — loading / error / empty states; RFC 7807 error codes
- [ ] Performance — expected data volume, pagination, N+1 risks

## 8. Security Considerations

Required when touching auth, provider keys, tenant boundaries, WebSocket, file upload,
or user-input processing. Otherwise "None — no sensitive surface touched."

## 9. Quality Notes

- **Existing debt** in touched files (do not imitate; do not silently fix — see FU-n).
- **Patterns to follow** — exemplar files for the idioms this change must match.
- **Reuse inventory** — existing helpers/composables/components to use instead of
  writing new ones.

## 10. Risks and Rollback

Known risks and mitigation. If a migration is involved: is it reversible, and what is
the rollback path?

## 11. Acceptance Criteria

- [ ] AC-1: ...
- [ ] AC-2: ...

## 12. Test Plan

How each AC gets verified: which test level (unit / component / integration / manual
via `verify`), and where the tests live.

## 13. SRS Delta

Drafted `[Rxx.yy]` entries, verbatim, ready to apply to `REQUIREMENTS.md` on approval.
"None" if this feature is already fully covered by existing requirements.

## 14. Open Questions

Unresolved items that do not block approval. Anything blocking belongs in §3 instead.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

Out-of-scope discoveries (FU-n), recorded, not fixed in this task.
