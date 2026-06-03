# Verification Report — PR2 (Slices 3-6)

**Change**: refactor-screaming-architecture
**Branch**: refactor/screaming-arch-pr2-features (stacked on PR1)
**Scope**: Slices 3-6 (analytics, comprobantes+delivery, messaging, cobranza)
**Date**: 2026-06-02
**Mode**: Strict TDD (baseline 310)
**Reviewer posture**: ADVERSARIAL
**Verdict**: NOT READY TO MERGE — 1 CRITICAL blocker

---

## Test Result

310 passed in 1.39s — matches baseline. No regressions.

---

## Completeness (PR2 scope: 21/21 tasks DONE)

Slice 3 analytics 6/6, Slice 4 comprobantes+delivery 5/5, Slice 5 messaging 5/5, Slice 6 cobranza 5/5.

---

## Runtime Import Check

ALL IMPORTS OK — features.analytics.dashboard, features.comprobantes.validator,
features.messaging.whatsapp_service, features.cobranza.tools,
shared.debt_math, shared.delivery.certificate_pdf — no circular imports.

---

## Dependency Rule Audit — ALL PASS

shared/ imports features/: 0 hits
shared/ imports tenancy/: 0 hits
shared/ imports api/: 0 hits
tenancy/ imports features/: 0 hits
analytics cross-feature: 0 hits
comprobantes cross-feature: 0 hits
messaging cross-feature: 0 hits
cobranza cross to OTHER features: 0 hits
(intra-cobranza imports are not violations)

---

## CRITICAL (1)

### C1 — validator.py has UNCOMMITTED fix: committed version imports from integrations.doris_debt_source

git diff apps/agent/features/comprobantes/validator.py shows working-tree change NOT committed:

  COMMITTED (broken): from integrations.doris_debt_source import classify_tipo, normalize_cci
  WORKING TREE (correct): from shared.debt_math import classify_tipo, normalize_cci

Commit 367a036 contains the stale integrations/ import.
After PR2, integrations/ is an empty shell — doris_debt_source.py was git mv-d to
features/cobranza/doris_debt_source.py. The committed import WILL FAIL on a clean checkout.
Tests pass locally only because the working-tree fix is applied but not staged.

Required action before merge:
  git add apps/agent/features/comprobantes/validator.py
  git commit (new commit: fix(comprobantes): use shared.debt_math in validator)

---

## WARNING (2)

### W1 — doris_debt_source.py re-exports classify_tipo/normalize_cci as dead real estate

features/cobranza/doris_debt_source.py has:
  from shared.debt_math import classify_tipo, normalize_cci  # noqa: F401 re-exported
No callers outside features/cobranza/ import these from doris_debt_source.
Both tools.py and validator.py import directly from shared.debt_math.
Remove in next commit touching this file. Non-blocking after C1 fix.

### W2 — test_chathub_comprobante.py: variable named cobranza is actually the validator module

Lines 158,198,229,262,295: set_provider, cobranza = runner_env
cobranza IS features.comprobantes.validator (fixture imports+patches validator, returns it).
Functionally correct (tests pass), but confusing for maintainers.
Action: rename cobranza -> validator in unpack lines. Non-blocking.

---

## SUGGESTION (1)

### S1 — PR1 shim dissolution tracked for slice 7

core/responses.py re-export shim still has 3 callers. Expected — dissolution is slice 7 PR3.

---

## Flagged Risks Resolution

shared/webhooks.py WhatsAppService: webhooks.py:135 uses Any type hint on whatsapp_service.
Still used by callers. Any avoids shared-features circular import. Legitimate, not dead code.

_COMPROBANTES_PATH monkeypatch: All 4 test files patch the live module-level variable
in features.comprobantes.validator. Confirmed correct. Tests pass.

---

## Leftover State After PR2 (Expected)

apps/agent/core/: 12 files intact (slice 7 moves them) — CORRECT
apps/agent/integrations/: __init__.py only (empty shell) — CORRECT
apps/agent/tools/: __init__.py only (empty shell) — CORRECT
No orphaned modules detected. Aligns with PR3 scope.

---

## Scope Discipline — ALL PASS

conversation not moved (slice 7): core/ intact
opportunity_detector.py not deleted (slice 7): still in core/
No lead->debtor rename (slice 8): debtor appears only in pre-existing comments/strings
No storage migration (slice 9): 0 migration files

---

## Git Discipline — ALL PASS

4 refactor commits + 1 chore commit, conventional format.
git log --follow on moved files shows pre-move history.
CLAUDE.md: untracked only (not staged).
No secrets staged.

---

## Final Verdict

NOT ready to merge as-is.

Blocking: C1 — commit the working-tree fix in validator.py (one git add + git commit).

After C1 resolved:
  0 CRITICAL
  2 WARNING (cosmetic, non-blocking)
  1 SUGGESTION (tracked for PR3)

Next: resolve C1, then sdd-archive PR2, then sdd-apply PR3 (slice 7).
