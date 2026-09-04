"""HESTA Member-Email Agentic Pilot — AgentCore Runtime entrypoint.

The pipeline (UNDERSTAND -> DECIDE -> EXECUTE -> LEARN) is declarative: the graph of
agents/deterministic steps lives in workflows/hesta.workflow.yaml (ADR-0015 decision 1)
and is executed by fabric.executor.GraphExecutor. This module wires the AgentCore
Runtime entrypoint to that graph and renders the SAME streamed markdown output as
before, via the executor's on_step hook. fabric/adapters.py and fabric/routers.py
register the graph's node/router implementations — see
docs/decisions/0015-config-driven-agent-fabric-orchestration.md.

Reuse decisions (see app/hesta-claimsagent/IMPLEMENTATION_PLAN.md §0): identity reuses the
existing DynamoDB check, the human hand-off is a DynamoDB record written via MCP, and the
Writer's draft is displayed — nothing is auto-sent. No AWS resources are created or changed.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from fabric import adapters, registry, routers  # noqa: F401 — import registers node/router implementations
from fabric.executor import GraphExecutor
from fabric.loader import load_fabric_config
from ingestion.email_normalizer import normalize_email
from intents import taxonomy
from memory.session import get_memory_session_manager
from tools import gateway

app = BedrockAgentCoreApp()
log = app.logger

_FABRIC_CONFIG = load_fabric_config(Path(__file__).parent / "workflows" / "hesta.workflow.yaml")
registry.bind(_FABRIC_CONFIG)


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


# ─── Graph-step renderers (map a completed node id to markdown, ADR-0015) ─────


def _render_intent_identifier(state: dict) -> str:
    return "## 1 · Understand\n\n" + _fmt_intents(state["intent_result"])


def _render_context_manager(state: dict) -> str:
    out = _fmt_summary(state["summary"])
    if state.get("memory_recorded") is not None:
        status = "recorded" if state["memory_recorded"] else "not recorded"
        out += f"_🧠 Memory active — actor `{state['actor_id']}` · session `{state['session_id']}` · turn {status}._\n\n"
    return out


def _render_case_status_lookup(state: dict) -> str:
    return _fmt_status(state["status_ctx"])


def _render_identity_profiling(state: dict) -> str:
    return "## 2 · Decide\n\n" + _fmt_profile(state["profile"])


def _render_attachment_validation(state: dict) -> str:
    return _fmt_attach(state["attachments"])


def _render_empathy(state: dict) -> str:
    return _fmt_empathy(state["empathy"])


def _render_routing_decision(state: dict) -> str:
    return _fmt_decision(state["decision"])


def _render_writer(state: dict) -> str:
    draft = state["draft"]
    out = [
        "## 3 · Execute\n\n",
        "### ✉️ Draft reply — for HESTA staff to review & send (NOT sent by the agent)\n\n",
        f"**Subject:** {draft.subject}\n\n",
        "```text\n" + draft.body + "\n```\n\n",
    ]
    if draft.assumptions:
        out.append("_Assumptions to confirm:_ " + "; ".join(draft.assumptions) + "\n\n")
    return "".join(out)


def _render_reviewer_editor(state: dict) -> str:
    return _fmt_review(state["review"])


def _render_hitl_record(state: dict) -> str:
    return "### 👤 Human-in-the-loop\n\n" + state["hitl_message"]


_RENDER_AFTER = {
    "intent_identifier": _render_intent_identifier,
    "context_manager": _render_context_manager,
    "case_status_lookup": _render_case_status_lookup,
    "identity_profiling": _render_identity_profiling,
    "attachment_validation": _render_attachment_validation,
    "empathy": _render_empathy,
    "routing_decision": _render_routing_decision,
    "writer": _render_writer,
    "reviewer_editor": _render_reviewer_editor,
    "hitl_record": _render_hitl_record,
}


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

    state = {
        "inbound": inbound,
        "mcp": mcp,
        "memory_session": memory_session,
        "actor_id": actor_id,
        "session_id": session_id,
    }

    try:
        # UNDERSTAND -> DECIDE -> EXECUTE run as the declarative graph in
        # workflows/hesta.workflow.yaml; on_step streams each section as its node
        # completes, matching the pre-fabric pipeline's incremental output exactly.
        queue: asyncio.Queue = asyncio.Queue()

        async def _on_step(node_id: str, current_state: dict) -> None:
            renderer = _RENDER_AFTER.get(node_id)
            if renderer:
                queue.put_nowait(renderer(current_state))

        async def _drive() -> None:
            try:
                await GraphExecutor(_FABRIC_CONFIG).run(state, on_step=_on_step)
            finally:
                queue.put_nowait(None)  # sentinel: no more sections

        driver = asyncio.ensure_future(_drive())
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
        await driver  # re-raise any exception the graph run hit

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
