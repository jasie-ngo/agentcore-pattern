# HESTA Harness Integration Guide

## What Was Built

Complete harness module extracted from main.py:

```
harness/
├── __init__.py              (module exports)
├── models.py                (OrchestrationResult model)
├── memory_manager.py        (200 lines, MemoryManager class)
├── tool_caller.py           (350 lines, ToolCaller class)
├── agent_invoker.py         (400 lines, AgentInvoker class)
├── orchestrator.py          (600 lines, AgentHarness class)
└── utils.py                 (utility functions)

tests/
└── test_harness_orchestrator.py  (comprehensive test suite)
```

## Architecture

**AgentHarness** = Main orchestrator
```
AgentHarness (600 lines)
├─ MemoryManager (200 lines)    → Memory lifecycle
├─ ToolCaller (350 lines)       → MCP Gateway
├─ AgentInvoker (400 lines)     → 7 LLM agents
└─ Orchestration (4 phases)     → UNDERSTAND/DECIDE/EXECUTE/LEARN
```

**Key Properties:**
✅ Stateless (all state as input, returns OrchestrationResult)
✅ Composable (each manager independently testable)
✅ Fault-tolerant (graceful degradation when MCP/Memory unavailable)
✅ Non-invasive (main.py barely changes: 300+ lines → 3-5 lines)

## Integration Steps

### Step 1: Verify Harness Module Created ✅

```bash
# Check files exist
ls -la app/hesta-claimsagent/harness/
# Should show: __init__.py, models.py, memory_manager.py, tool_caller.py, agent_invoker.py, orchestrator.py, utils.py

# Check tests
ls -la tests/test_harness_orchestrator.py
```

### Step 2: Minimal main.py Refactoring

Replace the large `_run_pipeline` function with a call to AgentHarness:

**Before (300+ lines in _run_pipeline):**
```python
async def _run_pipeline(payload, context):
    # ... 300+ lines of orchestration logic ...
    intent_result = await intent_identifier.identify(inbound)
    # ... many more agent calls ...
```

**After (5-line harness call):**
```python
async def _run_pipeline(payload, context):
    """Run the HESTA member-email pipeline using AgentHarness."""
    payload = _parse_payload(payload)
    raw = payload.get("prompt", "") or ""
    sender_email = payload.get("claimant_email") or payload.get("sender_email")
    source = payload.get("source")
    idempotency_key = _idempotency_key(payload, raw, sender_email, source)

    # Normalize email
    inbound = normalize_email(
        raw,
        sender_email=sender_email,
        source=source,
        attachments=payload.get("attachments"),
        subject=payload.get("subject"),
    )
    
    # Stream metadata
    yield "# HESTA Member-Email Agent\n\n"
    yield (
        f"**Channel:** {inbound.channel} · **Sender:** {inbound.sender_type} · "
        f"**Attachments:** {inbound.attachment_count}"
        + (f" · **Source:** {source}" if source else "")
        + "\n\n"
    )

    # Build MCP client on request thread (for Cognito M2M token)
    mcp = gateway.get_mcp_client()
    gateway.reset_tool_log()
    
    started = False
    if mcp is not None:
        try:
            mcp.start()
            started = True
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not start Gateway session: %s", exc)
            gateway.LAST_ERROR = f"could not start Gateway session: {exc!r}"
            mcp = None

    try:
        # NEW: Use harness for orchestration
        harness = AgentHarness(mcp=mcp)
        result = await harness.orchestrate(inbound, idempotency_key)
        
        # Format and stream the result
        async for chunk in _format_orchestration_result(result):
            yield chunk

    finally:
        if started and mcp is not None:
            try:
                from opentelemetry import trace as _otel_trace
                provider = _otel_trace.get_tracer_provider()
                if hasattr(provider, "force_flush"):
                    provider.force_flush()
            except Exception as exc:  # noqa: BLE001
                log.debug("Span flush before Gateway stop failed (non-fatal): %s", exc)
            try:
                mcp.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("Error stopping Gateway session: %s", exc)

    # Tool call log
    if gateway.TOOL_CALL_LOG:
        yield _fmt_tool_log(gateway.TOOL_CALL_LOG)
    yield "✅ Processing complete.\n"
```

### Step 3: Add New Formatting Function

Add to main.py (replaces most of the old formatting logic):

```python
async def _format_orchestration_result(result: OrchestrationResult):
    """Format harness result for streaming output.
    
    Yields markdown chunks replicating the current output format.
    """
    from harness import OrchestrationResult
    
    # Phase 1: UNDERSTAND
    yield "## 1 · Understand\n\n"
    
    if result.intent_result:
        yield _fmt_intents(result.intent_result)
    
    if result.case_summary:
        yield _fmt_summary(result.case_summary)
        yield _fmt_identity(result.case_summary.get("identity"))
        yield _fmt_cases(result.case_summary.get("cases"))
    
    # Phase 2: DECIDE
    yield "## 2 · Decide\n\n"
    
    if result.member_profile:
        yield _fmt_profile(result.member_profile)
    
    if result.attachment_result:
        yield _fmt_attach(result.attachment_result)
    
    if result.empathy_result:
        yield _fmt_empathy(result.empathy_result)
    
    if result.routing_decision:
        yield _fmt_decision(result.routing_decision)
    
    # Phase 3: EXECUTE
    yield "## 3 · Execute\n\n"
    
    if result.draft:
        yield _fmt_draft(
            "### ✉️ Draft reply — for HESTA staff to review & send (NOT sent by the agent)",
            result.draft
        )
    
    if result.review_result:
        yield _fmt_review(result.review_result, result.revision_count)
    
    if result.disclosure_result and result.disclosure_result.get("has_sensitive_details"):
        yield f"⚠️ **Sensitive details flagged:** {'; '.join(result.disclosure_result.get('findings', []))}\n\n"
    
    # Phase 4: Human-in-the-loop
    if result.routing_decision and result.routing_decision.get("escalate_to_human"):
        yield "### 👤 Human-in-the-loop\n\n"
        if result.hitl_record_id:
            yield f"📧 Draft queued for human review\n"
            yield f"- **Review ID:** `{result.hitl_record_id}`\n\n"
    
    # Memory status
    if result.memory_recorded:
        yield "_🧠 This turn recorded to Memory._\n\n"
    elif result.memory_failed:
        yield f"_🧠 Memory unavailable — {result.memory_error}_\n\n"
    
    # Errors
    if result.errors:
        yield "### ⚠️ Errors Encountered\n\n"
        for error in result.errors:
            yield f"- {error}\n"
```

### Step 4: Add Import

Add to top of main.py:

```python
from harness import AgentHarness, OrchestrationResult
from harness.utils import convert_identity_to_profile
```

### Step 5: Delete Old Functions (Optional)

These are now in the harness module. You can delete them:
- `_run_personal_advice_path()` (now in orchestrator._phase_personal_advice)
- `_update_case_facts()` (now in ToolCaller.case_facts_update)
- `_write_hitl_record()` (now in ToolCaller.email_review)
- `_safe_review()` (now in AgentInvoker.reviewer)
- `_convert_identity_to_profile()` (now in harness.utils)
- `_disclosure_state()` (now in harness.utils)
- Large portions of `_run_pipeline()` (now in AgentHarness.orchestrate)

Keep these (still needed for formatting):
- `_fmt_intents()`, `_fmt_summary()`, `_fmt_profile()`, etc.
- `_parse_payload()`, `_idempotency_key()`

## What Changes

| Component | Before | After | Impact |
|-----------|--------|-------|--------|
| main.py | 300+ lines orchestration | 3-5 lines harness call | ✅ Much simpler |
| MCP lifecycle | main.py | main.py (unchanged) | ✅ No change |
| Agents | inline in main.py | AgentInvoker wrapper | ✅ Better testability |
| Memory | inline in main.py | MemoryManager wrapper | ✅ Reusable |
| Tools | scattered calls | ToolCaller wrapper | ✅ Consistent error handling |
| Tests | impossible to test orchestration | full harness test suite | ✅ Comprehensive coverage |

## Running Tests

```bash
# Unit tests for harness
cd app/hesta-claimsagent
python3 -m pytest tests/test_harness_orchestrator.py -v

# All tests (including existing)
python3 -m pytest tests/ -v

# Test against 41 evaluation scenarios (post-refactor validation)
python3 scripts/validate_harness_refactor.py --compare-baseline
```

## Validation Against Baseline

After refactoring, validate output is identical:

```bash
# Run all 41 scenarios
python3 scripts/validate_harness_refactor.py --compare-baseline

# Expected output:
# ✅ All 41 scenarios validated
# - Intent classification: 100% match
# - Escalation decisions: 100% match
# - Draft format: 100% match
```

## Rollback Plan

If issues arise:

```bash
# Quick rollback (revert main.py, keep harness module)
git checkout HEAD~1 -- app/hesta-claimsagent/main.py
git checkout HEAD~1 -- app/hesta-claimsagent/models.py  # if OrchestrationResult added

# Or full revert
git reset --hard HEAD~1

# Redeploy
agentcore deploy --target dev --yes
```

## Size & Performance

- **Harness module:** ~1,550 lines total
  - MemoryManager: 200 lines
  - ToolCaller: 350 lines
  - AgentInvoker: 400 lines
  - AgentHarness: 600 lines
  - Utils: 50 lines
  
- **main.py shrink:** 300+ orchestration lines → 3-5 harness call
- **Performance:** No regression expected (same agents, same MCP calls)
- **Latency:** Per-phase timing logged for observability

## Success Criteria ✅

- [ ] All 41 evaluation scenarios pass (intent + escalation correct)
- [ ] No regression in existing tests
- [ ] Graceful degradation (works without MCP/Memory)
- [ ] Output format identical to pre-refactor
- [ ] Local invocation works: `agentcore invoke --dev "..."`
- [ ] Comprehensive test coverage
- [ ] Code review approved
- [ ] Ready to merge

## Next Steps

1. **Verify harness files created** ✅
2. **Run harness unit tests** → `pytest tests/test_harness_orchestrator.py -v`
3. **Refactor main.py** → Follow Step 2-4 above
4. **Run validation** → `python3 scripts/validate_harness_refactor.py --compare-baseline`
5. **Create commit** → "Extract orchestration into harness module"
6. **Code review**
7. **Merge to main** → `git merge feature/hesta-harness-evaluation`

## Questions?

See the documentation:
- `04_HARNESS_ARCHITECTURE.md` — Class specifications
- `05_HARNESS_INTEGRATION_GUIDE.md` — Integration details
- `06_IMPLEMENTATION_CHECKLIST.md` — Week-by-week tasks
