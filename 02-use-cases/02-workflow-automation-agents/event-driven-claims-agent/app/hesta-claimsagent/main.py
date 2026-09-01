"""HESTA Member-Email Agentic Pilot — AgentCore Runtime entrypoint.

Reworked from the original dual-agent claims demo into HESTA's member-email pipeline.
It runs the full agent set over each inbound "email" (any file dropped in the S3 inbox),
following the plan's UNDERSTAND → DECIDE → EXECUTE → LEARN flow:

  UNDERSTAND  AI-001 Intent Identifier · AI-002 Conversation Context Manager
  DECIDE      AI-003 Identity (REUSES lookup_policy/DynamoDB) · AI-004 Attachments · AI-005 Empathy
              → routing gate (human-in-the-loop)
  EXECUTE     AI-011 Writer → draft email DISPLAYED as output (never sent)
              AI-012 Reviewer & Editor → checks the draft
              → human-in-the-loop = WRITE a record to DynamoDB via the MCP Gateway
                 (reuses create_claim + request_human_review)
  LEARN       reuse existing Memory / observability (no new build)

Reuse decisions (see app/hesta-claimsagent/IMPLEMENTATION_PLAN.md §0): identity reuses the
existing DynamoDB check, the human hand-off is a DynamoDB record written via MCP, and the
Writer's draft is displayed — nothing is auto-sent. No AWS resources are created or changed.
"""

from __future__ import annotations

import json
import re
import uuid

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from config import ENABLE_HITL_RECORD
from ingestion.email_normalizer import normalize_email
from intents import taxonomy
from memory.session import get_memory_session_manager, record_interaction
from routing import decide
from tools import gateway

from agents import (
    attachment_validation,
    case_status,
    context_manager,
    empathy as empathy_agent,
    identity_profiling,
    intent_identifier,
    reviewer_editor,
    writer as writer_agent,
)

app = BedrockAgentCoreApp()
log = app.logger


# ─── Payload parsing (handles agentcore dev wrapping + S3 trigger payload) ────


def _parse_payload(payload):
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {"prompt": payload}
    if not isinstance(payload, dict):
        return {"prompt": str(payload)}
    # Unwrap agentcore dev's {"prompt": "<json>"} wrapper.
    if "prompt" in payload and "source" not in payload and "claimant_email" not in payload:
        value = payload["prompt"]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
    return payload


def _safe_memory_id(value: str | None, fallback: str = "anonymous") -> str:
    """Sanitize an identifier for AgentCore Memory actorId/sessionId.

    Memory rejects '@', '.', and other characters (e.g. raw emails), so map any input to
    [a-zA-Z0-9_-] and guarantee an alphanumeric first character.
    """
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", (value or "").strip())
    safe = re.sub(r"-{2,}", "-", safe).strip("-_")
    if not safe or not safe[0].isalnum():
        safe = f"{fallback}-{safe}".strip("-_") or fallback
    return safe[:200]


# ─── Formatting helpers (readable markdown for the streamed output) ───────────


def _fmt_intents(result) -> str:
    lines = ["### 🎯 Intent (AI-001)\n"]
    lines.append(f"- **Primary:** `{result.primary_intent_id}` ({taxonomy.name_for(result.primary_intent_id)})")
    lines.append(f"- **Sender type:** {result.sender_type}")
    for i in result.intents:
        lines.append(f"  - `{i.intent_id}` — {i.confidence}/100 · {i.rationale}")
    if result.needs_human_triage:
        lines.append("- ⚠️ flagged for human triage")
    if getattr(result, "personal_advice_requested", False):
        lines.append("- ⛔ **personal advice requested** — advice will be declined (Guardrail) and escalated to a human")
    return "\n".join(lines) + "\n\n"


def _fmt_summary(summary) -> str:
    items = ", ".join(summary.outstanding_items) or "none"
    return (
        "### 🧵 Context (AI-002)\n\n"
        f"- **Summary:** {summary.summary}\n"
        f"- **State:** {summary.conversation_state}\n"
        f"- **Outstanding:** {items}\n\n"
    )


def _fmt_status(status) -> str:
    lines = ["### 📁 Existing cases (list_pending_claims)\n", f"- {status.note}"]
    for c in status.member_pending:
        lines.append(
            f"  - `{c.get('claim_id', '?')}` · {c.get('category', 'n/a')} · pending_review"
            f" · created {c.get('created_at', 'n/a')}"
        )
    return "\n".join(lines) + "\n\n"


def _fmt_profile(profile) -> str:
    factors = ", ".join(profile.factors_matched) or "none"
    return (
        "### 🪪 Identity (AI-003 · reuses lookup_policy/DynamoDB)\n\n"
        f"- **Member/policy #:** {profile.member_number or '—'}\n"
        f"- **Record matched:** {profile.matched} · **factors:** {factors}\n"
        f"- **Verification:** **{profile.verification_level}** "
        f"(human verification required: {profile.verification_required})\n"
        f"- {profile.notes}\n\n"
    )


def _fmt_attach(attach) -> str:
    return (
        "### 📎 Attachments (AI-004)\n\n"
        f"- **Detected:** {attach.attachments_present} · **expected:** {attach.expected_document} · "
        f"**status:** {attach.status}\n"
        f"- {attach.notes}\n\n"
    )


def _fmt_empathy(emp) -> str:
    flags = ", ".join(emp.vulnerability_flags) or "none"
    return (
        "### 💛 Empathy (AI-005)\n\n"
        f"- **Sentiment:** {emp.sentiment} · **priority:** {emp.priority} · **complaint:** {emp.complaint_indicator}\n"
        f"- **Vulnerability:** {flags}\n"
        f"- {emp.recommended_attention}\n\n"
    )


def _fmt_decision(decision) -> str:
    if decision.escalate_to_human:
        reasons = "\n".join(f"  - {r}" for r in decision.reasons)
        return f"### 🚦 Routing\n\n- **Human-in-the-loop:** YES\n{reasons}\n\n"
    return "### 🚦 Routing\n\n- **Human-in-the-loop:** no — draft ready for a quick staff check\n\n"


def _fmt_tool_log(calls) -> str:
    """Complete per-call log of every MCP Gateway call (name + Input + Output).

    Shows ALL calls — the console "tools used" panel only captures the first (runtime-side,
    not controllable from agent code). Plain markdown (this renderer shows <details> literally).
    """
    return ""
    # out = [f"---\n## 🔧 MCP tool calls ({len(calls)})\n"]
    # for i, c in enumerate(calls, 1):
    #     out.append(f"### {i}. `{c['name']}`\n")
    #     out.append("**Input**\n")
    #     out.append("```json\n" + c["input"] + "\n```\n")
    #     out.append("**Output**\n")
    #     out.append("```\n" + c["output"] + "\n```\n")
    # return "\n".join(out) + "\n"


def _fmt_review(review) -> str:
    checks = f"accuracy={review.accuracy_ok} · tone={review.tone_ok} · compliance={review.compliance_ok}"
    out = [
        "### 🔎 Review (AI-012)\n",
        f"- **Approved for a human to send:** {review.approved_for_human_send} ({checks})",
    ]
    if review.edits:
        out.append(f"- **Suggested edits:** {review.edits}")
    if review.issues:
        out.append("- **Issues:** " + "; ".join(review.issues))
    return "\n".join(out) + "\n\n"


# ─── Human-in-the-loop: write a record to DynamoDB via the MCP Gateway ────────


async def _write_hitl_record(mcp, inbound, intent_result, profile, decision, draft) -> str:
    """Reuse create_claim + request_human_review to persist the case for a human.

    This is the pilot's human hand-off — a DynamoDB record via the existing Gateway
    tools. No new tables/tools. Non-fatal: surfaces the real Gateway error if it fails.
    """
    description = (inbound.latest_message or "").strip()[:1000] or draft.subject
    claim = await gateway.call_tool(
        mcp,
        "create_claim",
        {
            "policy_number": profile.member_number or "UNKNOWN",
            "description": description,
            "estimated_amount": 0,
            "category": intent_result.primary_intent_id,
            "status": "pending_review",
            "decision": "escalated",
        },
    )
    # Gateway/transport failure (auth, connection, Cedar deny surfaced as an exception).
    if isinstance(claim, dict) and "_gateway_error" in claim:
        return f"⚠️ Could not write the case record (Gateway error): {claim['_gateway_error']}\n\n"
    # The create_claim Lambda returns {"error": "..."} for validation/DDB failures.
    if isinstance(claim, dict) and claim.get("error"):
        return f"⚠️ create_claim returned an error: {claim['error']}\n\n"

    claim_id = claim.get("claim_id") if isinstance(claim, dict) else None
    if not claim_id:
        # Don't claim success we can't confirm — show exactly what came back.
        return f"⚠️ create_claim did not return a claim_id (record not confirmed). Raw response: {json.dumps(claim)[:600]}\n\n"

    lines = [f"📋 Case record written to DynamoDB (Claims): `{claim_id}`"]

    review = await gateway.call_tool(
        mcp,
        "request_human_review",
        {
            "claim_id": claim_id,
            "reason": "; ".join(decision.reasons) or "Manual review required",
            "estimated_amount": 0,
        },
    )
    if isinstance(review, dict) and "_gateway_error" in review:
        lines.append(f"⚠️ Review record not written (Gateway error): {review['_gateway_error']}")
    elif isinstance(review, dict) and review.get("error"):
        lines.append(f"⚠️ request_human_review returned an error: {review['error']}")
    else:
        lines.append("🔍 Review record written to DynamoDB (Reviews) — case is queued for a human.")
    return "\n".join(lines) + "\n\n"


# ─── Entrypoint ───────────────────────────────────────────────────────────────


@app.entrypoint
async def invoke(payload, context):
    """Run the HESTA member-email pipeline over one inbound email and stream the result."""
    payload = _parse_payload(payload)
    raw = payload.get("prompt", "") or ""
    sender_email = payload.get("claimant_email") or payload.get("sender_email")
    source = payload.get("source")

    # Phase 0 — deterministic normalisation (inside the agent; the Trigger Lambda is unchanged).
    inbound = normalize_email(raw, sender_email=sender_email, source=source)
    gateway.reset_tool_log()  # fresh MCP call log for this invocation

    # AgentCore Memory (AI-002): key the actor on the member/policy number if we have one,
    # else the sender email — so the Context Manager recalls this member's prior contacts.
    # Graceful: get_memory_session_manager returns None if MEMORY_ID isn't configured.
    actor_id = _safe_memory_id(inbound.member_number_for_lookup or inbound.from_email)
    session_id = f"hesta-{actor_id}-{uuid.uuid4().hex}"
    memory_session = None
    try:
        memory_session = get_memory_session_manager(session_id, actor_id)
    except Exception as exc:  # noqa: BLE001 — never let memory setup break processing
        log.warning("Memory unavailable (running without recall): %s", exc)

    yield "# HESTA Member-Email Agent\n\n"
    yield (
        f"**Channel:** {inbound.channel} · **Sender:** {inbound.sender_type} · "
        f"**Attachments:** {inbound.attachment_count}"
        + (f" · **Source:** {source}" if source else "")
        + "\n\n"
    )

    # Build + start the MCP Gateway client HERE, on the runtime request thread, so the
    # Cognito M2M token fetch has the workload-identity context (matches claimsagent).
    # Do NOT move this into a worker thread — the token exchange fails without that context.
    mcp = gateway.get_mcp_client()
    started = False
    if mcp is not None:
        try:
            mcp.start()
            started = True
        except Exception as exc:  # noqa: BLE001 — degrade gracefully; surface the reason
            log.warning("Could not start Gateway session: %s", exc)
            gateway.LAST_ERROR = f"could not start Gateway session: {exc!r}"
            mcp = None

    try:
        # ── UNDERSTAND ──────────────────────────────────────────────────────
        yield "## 1 · Understand\n\n"
        intent_result = await intent_identifier.identify(inbound)
        yield _fmt_intents(intent_result)
        # AI-002 uses AgentCore Memory (when available) to recall this member's prior contacts.
        summary = await context_manager.summarize(inbound, session_manager=memory_session)
        yield _fmt_summary(summary)
        if memory_session is not None:
            # Explicitly persist this contact (structured_output doesn't fire the session
            # manager's write hooks), so SEMANTIC/SUMMARIZATION records actually populate.
            recorded = record_interaction(actor_id, session_id, inbound.latest_message, summary.summary)
            status = "recorded" if recorded else "not recorded"
            yield (
                f"_🧠 Memory active — actor `{actor_id}` · session `{session_id}` · turn {status}._\n\n"
            )

        # For status/progress enquiries, pull the member's existing pending cases
        # (reuses the list_pending_claims Gateway tool) so the Writer can be specific.
        status_ctx = None
        if case_status.is_status_query(inbound, summary, intent_result):
            status_ctx = await case_status.lookup_pending(mcp, inbound)
            yield _fmt_status(status_ctx)

        # ── DECIDE ──────────────────────────────────────────────────────────
        yield "## 2 · Decide\n\n"
        # AI-003 reuses the existing lookup_policy Gateway tool on the started client.
        profile = await identity_profiling.profile(mcp, inbound, intent_result)
        yield _fmt_profile(profile)
        attach = attachment_validation.assess(inbound, intent_result)
        yield _fmt_attach(attach)
        emp = await empathy_agent.assess(inbound)
        yield _fmt_empathy(emp)
        decision = decide(intent_result, profile, emp)
        yield _fmt_decision(decision)

        # ── EXECUTE ─────────────────────────────────────────────────────────
        yield "## 3 · Execute\n\n"
        draft = await writer_agent.write(inbound, intent_result, profile, summary, emp, status_ctx=status_ctx)
        yield "### ✉️ Draft reply — for HESTA staff to review & send (NOT sent by the agent)\n\n"
        yield f"**Subject:** {draft.subject}\n\n"
        yield "```text\n" + draft.body + "\n```\n\n"
        if draft.assumptions:
            yield "_Assumptions to confirm:_ " + "; ".join(draft.assumptions) + "\n\n"

        review = await reviewer_editor.review(draft, intent_result, profile)
        yield _fmt_review(review)

        # Human-in-the-loop hand-off = write a record to DynamoDB via the MCP Gateway.
        if decision.escalate_to_human:
            yield "### 👤 Human-in-the-loop\n\n"
            if ENABLE_HITL_RECORD:
                yield await _write_hitl_record(mcp, inbound, intent_result, profile, decision, draft)
            else:
                yield (
                    "_HITL record disabled (ENABLE_HITL_RECORD=false). Escalation reasons: "
                    + "; ".join(decision.reasons)
                    + "_\n\n"
                )

        # ── LEARN ───────────────────────────────────────────────────────────
        yield "## 4 · Learn\n\n"
        yield (
            "_Observability via the existing OTEL traces; human edits to the draft can be captured to "
            "AgentCore Memory (reuse) as feedback — post-pilot._\n\n"
        )
    finally:
        if started and mcp is not None:
            # Flush pending OTEL spans (incl. the LAST Gateway call) BEFORE closing the MCP
            # session — otherwise the runtime's "tools used" capture drops the final tool span
            # (the reason the panel showed 2 of 3). This makes the panel match the actual calls.
            try:
                from opentelemetry import trace as _otel_trace

                provider = _otel_trace.get_tracer_provider()
                if hasattr(provider, "force_flush"):
                    provider.force_flush()
            except Exception as exc:  # noqa: BLE001 — flush is best-effort
                log.debug("Span flush before Gateway stop failed (non-fatal): %s", exc)
            try:
                mcp.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("Error stopping Gateway session: %s", exc)

    # Complete per-call log of every MCP Gateway call (input + output) made this invocation.
    if gateway.TOOL_CALL_LOG:
        yield _fmt_tool_log(gateway.TOOL_CALL_LOG)

    yield "✅ Processing complete.\n"


if __name__ == "__main__":
    app.run()
