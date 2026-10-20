# HESTA Member-Email Agent

**Channel:** contact_form · **Sender:** member (jane.doe@example.com) · **Attachments:** 0

## 1 · Understand
### 🎯 Intent (AI-001)
- **Primary:** `withdrawal_benefit_payment` (Withdrawal / Benefit Payment)
- **Sender type:** member
  - `withdrawal_benefit_payment` — 95/100 · Member explicitly states they submitted a withdrawal application three weeks ago and is following up to confirm receipt and check its status; form reason "Accessing super" corroborates this.

### 🧵 Context (AI-002)
- **Summary:** Member Jane Doe submitted a partial withdrawal application approximately three weeks ago. The application has not been reflected in Member Online and she is following up to confirm receipt and obtain a status update.
- **State:** chasing_update
- **Outstanding:**
  - Verify member identity before discussing any account details
  - Locate the withdrawal application in HESTA's systems to confirm receipt
  - Provide member with a status update on the withdrawal application

### 📁 Existing cases (list_pending_claims)
- No pending case on file for POL-12345 (0 pending total). Staff should also search by email (jane.doe@example.com) in case the account is linked under a different identifier.

---

## 2 · Decide
### 🪪 Identity (AI-003 · reuses lookup_policy/DynamoDB)
- **Member/policy #:** POL-12345
- **Record matched:** false · **factors:** none
- **Verification:** **unverified** (human verification required: true)
- Gateway lookup_policy tool was not available or configured; no factors could be matched. Identity must be confirmed by a human before any account details are disclosed.

### 📎 Attachments (AI-004)
- **Detected:** 0 · **expected:** none · **status:** not_applicable
- No supporting documents are required for a status enquiry of this type.

### 💛 Empathy (AI-005)
- **Sentiment:** neutral · **priority:** normal · **complaint:** false
- **Vulnerability:** none
- Acknowledge the three-week wait and check application status promptly. Member is seeking clarification, not distressed, but deserves timely resolution.

### 🚦 Routing
- **Human-in-the-loop:** YES
  - Regulated intent: Withdrawal / Benefit Payment — must be handled by a verified HESTA staff member
  - Identity not verified — Gateway lookup unavailable; no factors matched against POL-12345

---

## 3 · Execute
### ✉️ Draft reply — for HESTA staff to review & send (NOT sent by the agent)

**Subject:** Re: Withdrawal Application Follow-Up

```text
Thank you for contacting HESTA.

Hi Jane,

Thanks for getting in touch and for following up on your withdrawal application — we completely understand how important it is to have clarity on where things stand.

Before we're able to discuss any account details or locate your application, we first need to verify your identity. This is to keep your super safe and your personal information secure.

For us to proceed with your request, please reply to this email with:
- Member number
- Full name (including any middle names)
- Date of birth
- The address we have on our records

To assist us in locating your application, it would also be helpful if you could let us know:
- How you submitted your application (e.g. via Member Online, email, post, or fax)
- The approximate date you submitted it
- Whether you received any confirmation at the time of submission

Once your identity is confirmed, we'll search our records and do our best to locate your application and provide you with a status update as soon as possible.

We're here to help,
The team at HESTA

Issued by H.E.S.T. Australia Ltd ABN 66 006 818 695 AFSL 235249, Trustee of HESTA ABN 64 971 749 321. This information is general only and does not consider your objectives, financial situation or needs.
```

_Assumptions to confirm:_
- "Jane" used as first name based on form submission (Jane Doe) — confirm this matches the member's preferred name before sending.
- No pending case was found under POL-12345; staff should also search by email address (jane.doe@example.com) in case the account is indexed differently.
- The draft has been corrected per reviewer guidance: account-level search result removed (not to be disclosed to an unverified individual), and the premature "Member Online tip" removed until the issue is resolved.

### 🔎 Review (AI-012)
- **Approved for a human to send:** false (accuracy=⚠️ · tone=✅ · compliance=❌)
- **Suggested edits:**
  - **Required — remove account disclosure before verification:** The original draft stated "we were unable to locate a pending withdrawal application against your account" — this is account-level information that must not be shared with an unverified individual. Replaced with: *"Once your identity is confirmed, we'll search our records and do our best to locate your application."* ✅ Applied above.
  - **Recommended — remove premature channel advice:** The "tip for future applications" recommending Member Online was removed as it is presumptuous and potentially dismissive while the member's issue is still unresolved. ✅ Applied above.
- **Issues:**
  - ~~UNVERIFIED IDENTITY + ACCOUNT DISCLOSURE~~ — resolved in corrected draft above.
  - ~~PREMATURE ACCOUNT ACTION~~ — resolved in corrected draft above.
  - ~~UNSOLICITED CHANNEL ADVICE~~ — resolved in corrected draft above.

### 👤 Human-in-the-loop
Case record written to DynamoDB (Claims) under POL-12345, category `withdrawal_benefit_payment`, status `pending_review`. Review record queued for a human via `request_human_review`. Escalation reasons: (1) Regulated intent — Withdrawal / Benefit Payment; (2) Identity not verified — Gateway lookup unavailable, no factors matched.

---

## 4 · Learn
_Observability via the existing OTEL traces; human edits to the draft can be captured to AgentCore Memory as feedback — post-pilot._

✅ Processing complete.
  ✅  [main] complete  (46533 input, 3475 output tokens)