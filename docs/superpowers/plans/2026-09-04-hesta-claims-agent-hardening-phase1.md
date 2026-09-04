# Hesta Claims-Agent Hardening — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two highest-value, immediately-buildable gaps identified by the premortem against the real `hesta-claimsagent` pilot — the routing gate's blindness to attachment status, and the lack of a canary-testable model-configuration mechanism — without introducing new AWS infrastructure.

**Architecture:** Both tasks extend existing, real, already-deployed Python modules in place. Task 1 adds one parameter to the existing deterministic routing gate. Tasks 2–3 add an optional DynamoDB-backed override beneath the existing env-var model configuration, with graceful degradation (falls back to today's behaviour if the override table is absent or unreachable) — the same resilience pattern already used by `memory/session.py` and `tools/gateway.py` in this codebase.

**Tech Stack:** Python 3.12, pydantic (existing `models.py`), boto3 (DynamoDB), unittest (matches existing `tests/` convention).

**Spec:** `docs/hesta-claims-agent-spec.md` §3.2 (attachment routing fix) and §3.9 (model configuration), reconciled against `02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/app/hesta-claimsagent/IMPLEMENTATION_PLAN.md`.

## Global Constraints

- No new AWS infrastructure beyond one new DynamoDB table (`ModelRouting`) — everything else is application code, per `IMPLEMENTATION_PLAN.md` §0's "no AWS infrastructure altered" pilot philosophy (this plan's one exception is scoped and named, not silent).
- Every change must degrade gracefully: if the new DynamoDB table doesn't exist or a lookup fails, behaviour must be identical to today's — never raise, never block a case.
- Follow the existing test convention: `unittest`, `sys.path.insert` to the module under test, no AWS/Strands/LLM required to run.
- Do not touch `app/claimsagent` (the original generic demo) — all changes are scoped to `app/hesta-claimsagent`.

## Scope note (read before starting)

This plan deliberately does **not** include: wiring per-case model-variant selection into each of the 5 agent modules (`empathy.py`, `context_manager.py`, `writer.py`, `reviewer_editor.py`, `intent_identifier.py`) or writing the served variant back to the Claims/Reviews case record. Those modules cache their `Agent` object as a process-lifetime singleton (confirmed in `agents/empathy.py:32-39` and identical patterns in the other four) — correctly wiring a per-case canary draw into that caching requires touching all 5 files' caching logic plus the `create_claim`/`request_human_review` Lambda schemas for attribution, which is independent, larger, higher-risk work belonging to its own plan. This plan builds the override mechanism (Tasks 2–3) up to the point where that follow-up plan can consume it via `build_agent(..., model_id_override=...)` — a stable, tested seam — without touching the 5 agent modules or any Lambda.

Also out of scope for this plan (separate future plans per the premortem remediation ordering): the batch-evaluation/DevOps gate build-out (no implementation exists yet in the repo), the identity post-pilot graduation (`MembersTable` rename + `email-index` GSI — infra change), and the AgentCore Memory user-preference strategy / case-type pseudo-actor correction writes.

This plan also deliberately keys the model-routing override by **role** (`"fast"`/`"strong"`) rather than by individual agent name, diverging from `docs/hesta-claims-agent-spec.md` §3.9's per-agent-name design — this is because `agents/base.py`'s `_build_model` only distinguishes fast-vs-strong today, not individual agents, and this plan does not touch that caching/dispatch logic (see above). A follow-up wanting a true per-agent canary (e.g. canarying only the Writer) will need to change `resolve_model_variant`'s signature and the DynamoDB partition key, not just wire in the existing seam.

---

### Task 1: Wire attachment status into the routing gate

**Files:**
- Modify: `app/hesta-claimsagent/routing.py:31-65` (the `decide` function)
- Modify: `app/hesta-claimsagent/main.py:326` (the call site)
- Test: `tests/test_hesta_routing.py` (new file)

**Interfaces:**
- Consumes: `models.AttachmentAssessment` (existing, unchanged) — fields `attachments_present: int`, `expected_document: str`, `status: str` (one of `"not_applicable" | "present_unverified" | "missing"`, per `agents/attachment_validation.py`'s actual output), `notes: str`.
- Produces: `routing.decide(intent_result, profile, empathy, attach) -> RoutingDecision` — same `RoutingDecision` shape as today (`escalate_to_human: bool`, `reasons: list[str]`, `regulated: bool`), with attachment-driven escalation added.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hesta_routing.py`:

```python
"""Tests for the HESTA pilot's routing gate (app/hesta-claimsagent/routing.py).

Run:
    python3 -m unittest tests.test_hesta_routing -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from models import AttachmentAssessment, EmpathyAssessment, IntentResult, DetectedIntent, MemberProfile  # noqa: E402
from routing import decide  # noqa: E402


def _intent(primary="change_of_details", confidence=90, needs_triage=False):
    return IntentResult(
        intents=[DetectedIntent(intent_id=primary, confidence=confidence, rationale="test", evidence_quote="")],
        primary_intent_id=primary,
        sender_type="member",
        needs_human_triage=needs_triage,
    )


def _profile(verified=True):
    return MemberProfile(
        member_number="M1",
        matched=True,
        verification_level="verified" if verified else "unverified",
        verification_required=not verified,
    )


def _empathy():
    return EmpathyAssessment(sentiment="neutral", priority="normal")


class AttachmentRoutingTests(unittest.TestCase):
    def test_missing_required_attachment_escalates(self):
        attach = AttachmentAssessment(
            attachments_present=0,
            expected_document="bank statement / evidence of hardship",
            status="missing",
            notes="Expected a bank statement, none detected.",
        )
        decision = decide(_intent(primary="financial_hardship"), _profile(), _empathy(), attach)
        self.assertTrue(decision.escalate_to_human)
        self.assertTrue(any("missing" in r.lower() or "attachment" in r.lower() for r in decision.reasons))

    def test_present_unverified_does_not_force_escalation_alone(self):
        # Pilot can't validate content — presence-but-unverified alone shouldn't force
        # escalation on its own (it's a note for staff, not a hard gate).
        attach = AttachmentAssessment(
            attachments_present=1,
            expected_document="bank statement / evidence of hardship",
            status="present_unverified",
            notes="1 attachment detected; expected a bank statement.",
        )
        decision = decide(_intent(primary="rollover_transfer_combine"), _profile(), _empathy(), attach)
        # Non-regulated, verified, high confidence, no vulnerability, attachment present
        # (even if unverified) → no escalation reason should come from attachment status.
        self.assertFalse(any("attachment" in r.lower() for r in decision.reasons))

    def test_not_applicable_does_not_escalate(self):
        attach = AttachmentAssessment(
            attachments_present=0, expected_document="none", status="not_applicable", notes="none expected"
        )
        decision = decide(_intent(primary="rollover_transfer_combine"), _profile(), _empathy(), attach)
        self.assertFalse(any("attachment" in r.lower() for r in decision.reasons))

    def test_missing_attachment_escalates_even_when_otherwise_clean(self):
        # Non-regulated intent, verified, high confidence, no vulnerability — the ONLY
        # escalation trigger is the missing attachment. Proves attachment status alone
        # can now drive escalate_to_human=True (the gap this task closes).
        attach = AttachmentAssessment(
            attachments_present=0,
            expected_document="court order / legal documents",
            status="missing",
            notes="Expected court order, none detected.",
        )
        decision = decide(_intent(primary="rollover_transfer_combine"), _profile(), _empathy(), attach)
        self.assertTrue(decision.escalate_to_human)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent" && python3 -m unittest tests.test_hesta_routing -v`
Expected: FAIL — `decide()` takes 3 positional arguments but 4 were given (TypeError), for all four tests.

- [ ] **Step 3: Modify `routing.py` to accept and act on the attachment assessment**

In `app/hesta-claimsagent/routing.py`, change the import line and the `decide` function:

```python
from models import AttachmentAssessment, EmpathyAssessment, IntentResult, MemberProfile, RoutingDecision
```

Replace the `decide` function body:

```python
def decide(
    intent_result: IntentResult,
    profile: MemberProfile,
    empathy: EmpathyAssessment,
    attach: AttachmentAssessment,
) -> RoutingDecision:
    reasons: list[str] = []
    primary = intent_result.primary_intent_id
    regulated = taxonomy.is_regulated(primary)

    if regulated:
        reasons.append(f"regulated intent ({taxonomy.name_for(primary)})")

    # Personal advice must never be handled autonomously — always route to a human,
    # who is instructed not to provide personal advice either.
    if getattr(intent_result, "personal_advice_requested", False):
        reasons.append("PERSONAL ADVICE requested — do NOT provide personal financial advice")

    if profile.verification_required:
        reasons.append(f"identity not verified ({profile.verification_level})")

    confidence = _primary_confidence(intent_result)
    if primary == taxonomy.OTHER_UNKNOWN or intent_result.needs_human_triage:
        reasons.append("intent unclear / flagged for triage")
    elif confidence < INTENT_CONFIDENCE_THRESHOLD:
        reasons.append(f"low intent confidence ({confidence} < {INTENT_CONFIDENCE_THRESHOLD})")

    confident_intents = [i for i in intent_result.intents if i.confidence >= INTENT_CONFIDENCE_THRESHOLD]
    if len(confident_intents) > 1:
        reasons.append(f"multiple intents detected ({len(confident_intents)})")

    if empathy.vulnerability_flags or empathy.priority in ("high", "urgent"):
        flags = ", ".join(empathy.vulnerability_flags) or empathy.priority
        reasons.append(f"vulnerability/priority ({flags})")

    # A required document was expected but not detected — a human must chase it up
    # rather than the case silently proceeding as if nothing were missing.
    if attach.status == "missing":
        reasons.append(f"missing expected attachment ({attach.expected_document})")

    return RoutingDecision(escalate_to_human=bool(reasons), reasons=reasons, regulated=regulated)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent" && python3 -m unittest tests.test_hesta_routing -v`
Expected: PASS — all four tests green.

- [ ] **Step 5: Update the call site in `main.py`**

In `app/hesta-claimsagent/main.py`, line 326, change:

```python
        decision = decide(intent_result, profile, emp)
```

to:

```python
        decision = decide(intent_result, profile, emp, attach)
```

(`attach` is already computed on line 322, immediately above — no new variable needed.)

- [ ] **Step 6: Run the full existing test suite to check for regressions**

Run: `cd "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent" && python3 -m unittest discover -s tests -v`
Expected: PASS — existing `test_routing.py` (a different module, `app/claimsagent/routing.py`) is untouched and unaffected; new `test_hesta_routing.py` passes.

- [ ] **Step 7: Commit**

```bash
git add "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/app/hesta-claimsagent/routing.py" \
        "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/app/hesta-claimsagent/main.py" \
        "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/tests/test_hesta_routing.py"
git commit -m "fix: escalate to human when a required attachment is missing

routing.decide() previously ignored AttachmentAssessment entirely, so a
missing required document (e.g. a Financial Hardship case with no bank
statement) had no path to forcing human review. Closes premortem finding D."
```

---

### Task 2: Add a DynamoDB-backed model-routing override function to `config.py`

**Files:**
- Modify: `app/hesta-claimsagent/config.py` (append new section)
- Test: `tests/test_hesta_model_routing.py` (new file)

**Interfaces:**
- Consumes: an optional DynamoDB table (`MODEL_ROUTING_TABLE` env var names it; table schema: partition key `role` (string, `"fast"` or `"strong"`), attributes `primaryModelId` (string), `canaryModelId` (string, optional), `canaryPercent` (number, optional, 0-100)).
- Produces: `config.resolve_model_variant(role: str, seed: str) -> tuple[str, str]` — returns `(model_id, variant_label)` where `variant_label` is `"primary"` or `"canary"`. Task 3 consumes this exact signature.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hesta_model_routing.py`:

```python
"""Tests for config.resolve_model_variant (app/hesta-claimsagent/config.py).

Run:
    python3 -m unittest tests.test_hesta_model_routing -v
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

import config  # noqa: E402


class ResolveModelVariantNoTableTests(unittest.TestCase):
    """When MODEL_ROUTING_TABLE is unset (today's default), always return the env-var default."""

    def test_fast_role_returns_fast_default(self):
        with patch.object(config, "MODEL_ROUTING_TABLE", ""):
            model_id, variant = config.resolve_model_variant("fast", seed="case-123")
            self.assertEqual(model_id, config.FAST_MODEL_ID)
            self.assertEqual(variant, "primary")

    def test_strong_role_returns_strong_default(self):
        with patch.object(config, "MODEL_ROUTING_TABLE", ""):
            model_id, variant = config.resolve_model_variant("strong", seed="case-123")
            self.assertEqual(model_id, config.AGENT_MODEL_ID)
            self.assertEqual(variant, "primary")


class ResolveModelVariantWithTableTests(unittest.TestCase):
    """When a table is configured, canaryPercent controls a deterministic per-seed split."""

    def _mock_item(self, primary="primary-model", canary="canary-model", pct=30):
        return {"role": "fast", "primaryModelId": primary, "canaryModelId": canary, "canaryPercent": pct}

    def test_no_item_for_role_falls_back_to_default(self):
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch("config._get_model_routing_item", return_value=None):
            model_id, variant = config.resolve_model_variant("fast", seed="case-123")
            self.assertEqual(model_id, config.FAST_MODEL_ID)
            self.assertEqual(variant, "primary")

    def test_zero_canary_percent_always_primary(self):
        item = self._mock_item(pct=0)
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch("config._get_model_routing_item", return_value=item):
            model_id, variant = config.resolve_model_variant("fast", seed="any-seed")
            self.assertEqual(model_id, "primary-model")
            self.assertEqual(variant, "primary")

    def test_hundred_percent_canary_always_canary(self):
        item = self._mock_item(pct=100)
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch("config._get_model_routing_item", return_value=item):
            model_id, variant = config.resolve_model_variant("fast", seed="any-seed")
            self.assertEqual(model_id, "canary-model")
            self.assertEqual(variant, "canary")

    def test_same_seed_is_deterministic(self):
        item = self._mock_item(pct=50)
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch("config._get_model_routing_item", return_value=item):
            first = config.resolve_model_variant("fast", seed="case-456")
            second = config.resolve_model_variant("fast", seed="case-456")
            self.assertEqual(first, second)

    def test_lookup_exception_degrades_to_default(self):
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch("config._get_model_routing_item", side_effect=RuntimeError("dynamodb unavailable")):
            model_id, variant = config.resolve_model_variant("strong", seed="case-789")
            self.assertEqual(model_id, config.AGENT_MODEL_ID)
            self.assertEqual(variant, "primary")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent" && python3 -m unittest tests.test_hesta_model_routing -v`
Expected: FAIL — `config` has no attribute `MODEL_ROUTING_TABLE` / `resolve_model_variant` / `_get_model_routing_item` (AttributeError).

- [ ] **Step 3: Implement in `config.py`**

Append to the end of `app/hesta-claimsagent/config.py`:

```python
# ─── Model routing override (canary testing) ──────────────────────────────────
# Optional DynamoDB table for per-role model overrides, layered beneath the
# AGENT_MODEL_ID/FAST_MODEL_ID env-var defaults above. Empty ⇒ disabled, use env
# vars only (today's behaviour, unchanged). Table schema: partition key `role`
# ("fast" | "strong"), attributes `primaryModelId`, `canaryModelId` (optional),
# `canaryPercent` (0-100, optional).
MODEL_ROUTING_TABLE = os.getenv("MODEL_ROUTING_TABLE", "")

import hashlib
import logging

_model_routing_log = logging.getLogger(__name__)


def _get_model_routing_item(role: str) -> dict | None:
    """Fetch the override item for a role. Isolated for easy test mocking."""
    import boto3

    table = boto3.resource("dynamodb", region_name=REGION).Table(MODEL_ROUTING_TABLE)
    response = table.get_item(Key={"role": role})
    return response.get("Item")


def resolve_model_variant(role: str, seed: str) -> tuple[str, str]:
    """Resolve which model id to use for a role, honouring any canary override.

    Args:
        role: "fast" or "strong" — matches agents/base.py's existing fast/strong split.
        seed: a stable per-case identifier (e.g. the case's actor/session id) used to
            deterministically bucket the same case into the same variant every time it's
            evaluated, rather than an independent random draw per call.

    Returns:
        (model_id, variant_label) where variant_label is "primary" or "canary".
        Never raises — any failure degrades to the existing env-var default with
        variant_label="primary", matching today's behaviour exactly.
    """
    default_id = FAST_MODEL_ID if role == "fast" else AGENT_MODEL_ID
    if not MODEL_ROUTING_TABLE:
        return default_id, "primary"

    try:
        item = _get_model_routing_item(role)
    except Exception as exc:  # noqa: BLE001 — routing override is best-effort
        _model_routing_log.warning("Model routing lookup failed for role=%s (using default): %s", role, exc)
        return default_id, "primary"

    if not item:
        return default_id, "primary"

    primary_id = item.get("primaryModelId") or default_id
    canary_id = item.get("canaryModelId")
    canary_pct = int(item.get("canaryPercent", 0) or 0)

    if not canary_id or canary_pct <= 0:
        return primary_id, "primary"
    if canary_pct >= 100:
        return canary_id, "canary"

    bucket = int(hashlib.sha256(f"{role}:{seed}".encode()).hexdigest(), 16) % 100
    if bucket < canary_pct:
        return canary_id, "canary"
    return primary_id, "primary"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent" && python3 -m unittest tests.test_hesta_model_routing -v`
Expected: PASS — all six tests green.

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `cd "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent" && python3 -m unittest discover -s tests -v`
Expected: PASS — no existing test imports `config` in a way this addition could break (additive only; `MODEL_ROUTING_TABLE` defaults to `""`, so `resolve_model_variant` is inert until a table is actually configured).

- [ ] **Step 6: Commit**

```bash
git add "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/app/hesta-claimsagent/config.py" \
        "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/tests/test_hesta_model_routing.py"
git commit -m "feat: add optional DynamoDB canary override for model routing

resolve_model_variant(role, seed) layers a per-role primary/canary split
beneath the existing AGENT_MODEL_ID/FAST_MODEL_ID env vars, disabled by
default (MODEL_ROUTING_TABLE unset). Deterministic per-seed bucketing so
the same case always gets the same variant. Degrades to today's exact
behaviour on any lookup failure. Not yet wired into agent construction —
see Task 3."
```

---

### Task 3: Thread an optional model-id override through `build_agent`

**Files:**
- Modify: `app/hesta-claimsagent/agents/base.py`
- Test: `tests/test_hesta_agent_base.py` (new file)

**Interfaces:**
- Consumes: nothing new from Task 2 directly (kept decoupled — see rationale below); accepts a plain `model_id_override: str | None` parameter.
- Produces: `build_agent(system_prompt, *, fast=False, session_manager=None, guarded=False, model_id_override=None) -> Agent` — same return type as today (`Agent`), so none of the 5 existing call sites (`empathy.py`, `context_manager.py`, `writer.py`, `reviewer_editor.py`, `intent_identifier.py`) need to change for this task. This is the stable seam the follow-up plan (see Scope note) will call with `model_id_override=resolve_model_variant(...)[0]`.

**Rationale for keeping this decoupled from Task 2's function:** `build_agent` takes a `system_prompt` and returns a ready `Agent` — it has no natural "case seed" to pass to `resolve_model_variant` today (none of its callers have one to give it; see Scope note above). Rather than threading a seed parameter through all 5 call sites now (the larger, separately-scoped work), this task only proves `build_agent` *can* accept an externally-resolved model id, so the follow-up plan's job is purely "call `resolve_model_variant` in each agent module and pass the result here" — no `base.py` changes needed at that point.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hesta_agent_base.py`:

```python
"""Tests for agents.base.build_agent's model_id_override parameter.

Run:
    python3 -m unittest tests.test_hesta_agent_base -v
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

import config  # noqa: E402
from agents.base import _build_model  # noqa: E402


class BuildModelOverrideTests(unittest.TestCase):
    def test_no_override_uses_fast_default(self):
        model = _build_model(fast=True, guarded=False, model_id_override=None)
        self.assertEqual(model.config["model_id"], config.FAST_MODEL_ID)

    def test_no_override_uses_strong_default(self):
        model = _build_model(fast=False, guarded=False, model_id_override=None)
        self.assertEqual(model.config["model_id"], config.AGENT_MODEL_ID)

    def test_override_takes_precedence_over_fast(self):
        model = _build_model(fast=True, guarded=False, model_id_override="canary-model-xyz")
        self.assertEqual(model.config["model_id"], "canary-model-xyz")

    def test_override_takes_precedence_over_strong(self):
        model = _build_model(fast=False, guarded=False, model_id_override="canary-model-xyz")
        self.assertEqual(model.config["model_id"], "canary-model-xyz")


if __name__ == "__main__":
    unittest.main()
```

Note: `BedrockModel.config["model_id"]` assumes Strands' `BedrockModel` exposes its resolved config as a dict via `.config`. If this test fails with an `AttributeError` on `.config` in Step 2, check the installed `strands` package's actual `BedrockModel` attribute name (e.g. it may be `.model_id` directly) and adjust the assertion — the behaviour under test (which model id was passed to `BedrockModel(...)`) is unaffected either way.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent" && python3 -m unittest tests.test_hesta_agent_base -v`
Expected: FAIL — `_build_model()` doesn't accept `model_id_override` (TypeError).

- [ ] **Step 3: Modify `agents/base.py`**

Replace the `_build_model` and `build_agent` functions:

```python
def _build_model(fast: bool, guarded: bool, model_id_override: str | None = None) -> BedrockModel:
    model_id = model_id_override or (FAST_MODEL_ID if fast else AGENT_MODEL_ID)
    if guarded and GUARDRAIL_ID:
        # Attach the Bedrock Guardrail (no personal advice). Redacts blocked output to the
        # sentinel so the orchestrator can detect an intervention and fall back safely.
        return BedrockModel(
            model_id=model_id,
            guardrail_id=GUARDRAIL_ID,
            guardrail_version=GUARDRAIL_VERSION,
            guardrail_trace="enabled",
            guardrail_redact_output=True,
            guardrail_redact_output_message=GUARDRAIL_BLOCK_SENTINEL,
        )
    return BedrockModel(model_id=model_id)


def build_agent(
    system_prompt: str,
    *,
    fast: bool = False,
    session_manager=None,
    guarded: bool = False,
    model_id_override: str | None = None,
) -> Agent:
    """Build a Strands agent.

    guarded=True attaches the Bedrock Guardrail (when GUARDRAIL_ID is configured) — used by
    the Writer so it cannot emit personal financial advice.

    model_id_override, if given, takes precedence over the fast/strong env-var default —
    the seam a per-case canary-routing caller uses (see config.resolve_model_variant).
    """
    kwargs = {"model": _build_model(fast, guarded, model_id_override), "system_prompt": system_prompt}
    if session_manager is not None:
        # Attaching a session manager makes the agent record turns to AgentCore Memory and
        # recall relevant history for this actor/session.
        kwargs["session_manager"] = session_manager
    return Agent(**kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent" && python3 -m unittest tests.test_hesta_agent_base -v`
Expected: PASS — all four tests green (adjusting the `.config` assertion per the Step 1 note if needed).

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `cd "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent" && python3 -m unittest discover -s tests -v`
Expected: PASS — `model_id_override` defaults to `None` everywhere, so all 5 existing call sites (which don't pass it) are unaffected.

- [ ] **Step 6: Commit**

```bash
git add "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/app/hesta-claimsagent/agents/base.py" \
        "02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/tests/test_hesta_agent_base.py"
git commit -m "feat: let build_agent accept an explicit model-id override

Additive, backward-compatible seam for per-case canary model routing.
No existing call site is affected (model_id_override defaults to None).
Wiring config.resolve_model_variant's output into each of the 5 agent
modules' per-variant caching is separately scoped follow-up work."
```

---

## Self-review

**Spec coverage:** Task 1 implements spec §3.2's "immediate fix, independent of [attachment ingestion]." Tasks 2–3 implement the model-selection mechanism half of spec §3.9 (the DynamoDB override + `build_agent` seam); the remaining half of §3.9 (per-case attribution written back to the case record) and the full per-agent wiring are explicitly named as follow-up, not silently dropped — see the Scope note.

**Placeholder scan:** No TBD/TODO markers; every step has runnable code and an exact run command with an expected result.

**Type consistency:** `resolve_model_variant(role: str, seed: str) -> tuple[str, str]` (Task 2) matches its consumption in Task 3's docstring exactly. `build_agent`'s new `model_id_override: str | None = None` parameter name is consistent between its definition (Task 3) and its described future caller (Scope note). `routing.decide`'s new fourth positional parameter `attach: AttachmentAssessment` matches the `models.AttachmentAssessment` fields used in Task 1's tests exactly (`attachments_present`, `expected_document`, `status`, `notes`) and the real `status` enum values confirmed in `agents/attachment_validation.py` (`not_applicable`, `present_unverified`, `missing` — not `ok`, correcting the models.py docstring's stale claim).

---

Plan complete and saved to `docs/superpowers/plans/2026-09-04-hesta-claims-agent-hardening-phase1.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
