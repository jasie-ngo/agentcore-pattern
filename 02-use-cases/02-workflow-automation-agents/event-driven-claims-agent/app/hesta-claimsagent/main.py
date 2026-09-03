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
from opentelemetry import trace as otel_trace
from opentelemetry.trace import SpanKind
from config import ENABLE_HITL_RECORD
from ingestion.email_normalizer import normalize_email
from intents import taxonomy
from memory.session import get_memory_session_manager, record_interaction
from routing import decide
from tools import gateway

from agents import (
    attachment_validation,
    context_manager,
    empathy as empathy_agent,
    intent_identifier,
    reviewer_editor,
    writer as writer_agent,
)

app = BedrockAgentCoreApp()
log = app.logger

# Scope name must start with "opentelemetry.instrumentation." — AgentCore Evaluation's
# generic-framework support only reads spans under that prefix (or "openinference.instrumentation.").
_tracer = otel_trace.get_tracer("opentelemetry.instrumentation.hesta_claims_agent")


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


def _fmt_identity(identity) -> str:
    if identity.error:
        return f"### 🪪 Identity (AI-002: member_lookup)\n\n- ⚠️ {identity.error}\n\n"
    return (
        "### 🪪 Identity (AI-002: member_lookup)\n\n"
        f"- **Member ID:** {identity.member_id or '—'}\n"
        f"- **Email:** {identity.email or '—'}\n"
        f"- **Name:** {identity.name or '—'}\n"
        f"- **Status:** {identity.status or '—'}\n\n"
    )


def _fmt_cases(cases) -> str:
    if cases.error:
        return f"### 📁 Cases (AI-002: case_lookup_creation)\n\n- ⚠️ {cases.error}\n\n"
    if cases.status == "existing_cases_found":
        lines = [f"### 📁 Existing cases (case_lookup_creation)\n"]
        for c in cases.cases:
            lines.append(f"  - Case ID: `{c.get('case_id', '?')}` · Status: {c.get('status', 'n/a')}")
        return "\n".join(lines) + "\n\n"
    elif cases.status == "new_case_created":
        new = cases.new_case or {}
        return (
            "### 📁 New case created (case_lookup_creation)\n\n"
            f"- **Case ID:** `{new.get('case_id', '?')}`\n"
            f"- **Member ID:** {new.get('member_id', '?')}\n"
            f"- **Status:** {new.get('status', 'Open')}\n\n"
        )
    return ""


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


# ─── Convert identity info to MemberProfile for routing/writer ──────────────────


def _convert_identity_to_profile(identity, inbound, intent_result):
    """Convert IdentityInfo from member_lookup into MemberProfile for routing."""
    from models import MemberProfile

    if identity.error:
        return MemberProfile(
            member_number=inbound.member_number_for_lookup,
            matched=False,
            verification_level="unverified",
            verification_required=True,
            notes=identity.error,
        )

    return MemberProfile(
        member_number=identity.member_id,
        matched=True if identity.member_id else False,
        match_key="member_id" if identity.member_id else None,
        factors_matched=[k for k in ["member_id", "email"] if getattr(identity, k)],
        account_type=None,
        member_status=identity.status,
        verification_level="verified" if identity.status == "active" else "unverified",
        verification_required=False if identity.status == "active" else True,
        notes=f"Member lookup found: {identity.member_id}",
    )


# ─── Human-in-the-loop: write a record to DynamoDB via the MCP Gateway ────────


async def _write_hitl_record(mcp, inbound, intent_result, profile, decision, draft, cases) -> str:
    """Write draft for human review via email_review tool.

    Gets the case_id from case_lookup_creation result (in cases), then calls
    email_review to create a review record in the human review table.
    Non-fatal: surfaces the real Gateway error if it fails.
    """
    # Get case_id from case_lookup_creation result
    case_id = None
    if cases.status == "existing_cases_found" and cases.cases:
        case_id = cases.cases[0].get("case_id")
    elif cases.status == "new_case_created" and cases.new_case:
        case_id = cases.new_case.get("case_id")

    if not case_id:
        return "⚠️ No case_id available for email review (case lookup failed).\n\n"

    review = await gateway.call_tool(
        mcp,
        "email_review",
        {
            "case_id": case_id,
            "draft_subject": draft.subject,
            "draft_body": draft.body,
            "escalation_reasons": "; ".join(decision.reasons) or "Manual review required",
        },
    )

    if isinstance(review, dict) and "_gateway_error" in review:
        return f"⚠️ Could not write review record (Gateway error): {review['_gateway_error']}\n\n"
    if isinstance(review, dict) and review.get("error"):
        return f"⚠️ email_review returned an error: {review['error']}\n\n"

    review_id = review.get("review_id") if isinstance(review, dict) else None
    if not review_id:
        return f"⚠️ email_review did not return a review_id. Raw response: {json.dumps(review)[:600]}\n\n"

    return (
        f"📧 Draft queued for human review\n\n"
        f"- **Case ID:** `{case_id}`\n"
        f"- **Review ID:** `{review_id}`\n"
        f"- **Escalation reasons:** {'; '.join(decision.reasons) or 'Manual review required'}\n\n"
    )


# ─── Entrypoint ───────────────────────────────────────────────────────────────


@app.entrypoint
async def invoke(payload, context):
    """Entrypoint wrapper: opens the session's one required `invoke_agent` span.

    AgentCore Evaluation needs exactly one span per session tagged
    gen_ai.operation.name=invoke_agent to know what to evaluate. _run_pipeline's sub-agents
    only ever emit execute_structured_output spans (Agent.structured_output_async), so
    without this wrapper no session ever has an invoke_agent span and every evaluator fails
    with "no spans to evaluate".
    """
    raw = _parse_payload(payload).get("prompt", "") or ""
    output_chunks: list[str] = []
    with _tracer.start_as_current_span(
        "invoke_agent",
        kind=SpanKind.SERVER,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "HestaMemberEmailAgent",
            "gen_ai.task.input": raw,
        },
    ) as agent_span:
        async for chunk in _run_pipeline(payload, context):
            output_chunks.append(chunk)
            yield chunk
        agent_span.set_attribute("gen_ai.task.output", "".join(output_chunks))


async def _run_pipeline(payload, context):
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
        # AI-002: Context Manager now pulls identity + cases via member_lookup & case_lookup_creation
        summary = await context_manager.summarize(inbound, mcp=mcp, session_manager=memory_session)
        yield _fmt_summary(summary)
        yield _fmt_identity(summary.identity)
        yield _fmt_cases(summary.cases)
        if memory_session is not None:
            # Explicitly persist this contact (structured_output doesn't fire the session
            # manager's write hooks), so SEMANTIC/SUMMARIZATION records actually populate.
            recorded = record_interaction(actor_id, session_id, inbound.latest_message, summary.summary)
            status = "recorded" if recorded else "not recorded"
            yield (
                f"_🧠 Memory active — actor `{actor_id}` · session `{session_id}` · turn {status}._\n\n"
            )

        status_ctx = summary.cases

        # ── DECIDE ──────────────────────────────────────────────────────────
        yield "## 2 · Decide\n\n"
        # AI-003 is now part of context_manager (member_lookup), convert to MemberProfile for routing
        profile = _convert_identity_to_profile(summary.identity, inbound, intent_result)
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

        # Human-in-the-loop hand-off = write a record to DynamoDB via email_review tool.
        if decision.escalate_to_human:
            yield "### 👤 Human-in-the-loop\n\n"
            if ENABLE_HITL_RECORD:
                yield await _write_hitl_record(mcp, inbound, intent_result, profile, decision, draft, status_ctx)
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
