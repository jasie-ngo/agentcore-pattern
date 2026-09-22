"""Trigger Lambda: S3 email → EventBridge → Invoke Agent Runtime (fire-and-forget).

The Runtime uses IAM (SigV4) authentication. This Lambda's execution role has
bedrock-agentcore:InvokeAgentRuntime permission granted by CDK.

The invocation is fire-and-forget: the Lambda sends the signed HTTPS request
and confirms the Runtime accepted it (HTTP 200), but does NOT wait for the full
streaming response. The agent processes the claim asynchronously — results are
written to DynamoDB by the agent's tool calls, not returned to this Lambda.

Transport (TODO 2 / design decision D1): an inbound email may arrive as a single
``.eml`` MIME object (body + attachments as MIME parts), parsed here with the
standard library ``email`` package — nothing new is installed. A plain ``.txt``
object (the pre-existing HESTA contact-form / direct-email shapes) keeps its
original text-based parsing path unchanged, and always yields ``attachments: []``.
"""

import email
import email.policy
import email.utils
import json
import logging
import os
import re
import urllib.parse
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session as BotocoreSession

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")

# Environment variables (set by CDK)
RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

# Attachment bounds (TODO 2 rule 4) — fail closed rather than crash on a bad env var.
def _bounded_int(env_var: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(env_var, str(default))))
    except (TypeError, ValueError):
        return default


MAX_ATTACHMENT_BYTES = _bounded_int("MAX_ATTACHMENT_BYTES", 262144)
MAX_ATTACHMENTS = _bounded_int("MAX_ATTACHMENTS", 5)


def invoke_runtime_async(payload_dict):
    """Invoke the AgentCore Runtime via HTTPS with SigV4 auth (fire-and-forget).

    Sends the request and reads only the first chunk to confirm acceptance.
    Does NOT buffer the full streaming response — the agent processes
    asynchronously and writes results to DynamoDB via tool calls.
    """
    if not RUNTIME_ARN:
        raise RuntimeError("AGENTCORE_RUNTIME_ARN is not configured")
    escaped_arn = urllib.parse.quote(RUNTIME_ARN, safe="")
    url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{escaped_arn}/invocations"

    payload = json.dumps(payload_dict).encode()

    # Sign the request with SigV4 using the Lambda's execution role credentials
    session = BotocoreSession()
    credentials = session.get_credentials().get_frozen_credentials()

    aws_request = AWSRequest(
        method="POST",
        url=url,
        data=payload,
        headers={
            "Content-Type": "application/json",
        },
    )
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(aws_request)

    req = urllib.request.Request(
        url,
        data=payload,
        headers=dict(aws_request.headers),
    )
    # Fire-and-forget: open the connection, confirm HTTP 200, read first few
    # lines to verify the agent started, then close without waiting for completion.
    # Timeout covers the Runtime cold start (~30-60s on first invocation).
    if not url.startswith("https://"):
        raise ValueError(f"Only HTTPS URLs are permitted: {url}")

    with urllib.request.urlopen(req, timeout=65) as resp:  # nosec B310  # 65s covers cold start
        print("STATUS:", resp.status)
        print("HEADERS:", dict(resp.headers))
        status = resp.status
        # Read up to 5 lines to confirm the agent started streaming
        preview_lines = []
        for i, line in enumerate(resp):
            if i >= 5:
                break
            decoded = line.decode("utf-8").strip()
            if decoded:
                preview_lines.append(decoded)

    return status, preview_lines


def parse_email(content):
    """Parse email-format text into structured fields."""
    headers = {}
    lines = content.split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip() == "":
            body_start = i + 1
            break
        match = re.match(r"^(From|Subject|Date|To):\s*(.+)$", line, re.IGNORECASE)
        if match:
            headers[match.group(1).lower()] = match.group(2).strip()
    body = "\n".join(lines[body_start:]).strip()
    return headers, body


def parse_hesta_form(content):
    """Parse Hesta form-based email format into structured fields."""
    fields = {}
    lines = content.split("\n")

    for line in lines:
        if " : " in line:
            key, value = line.split(" : ", 1)
            key = key.strip().lower()
            value = value.strip()
            fields[key] = value

    return fields


def is_hesta_form_format(content):
    """Check if content is a Hesta form-based email."""
    return "form based mail" in content.lower() and "Values:" in content


def is_email_format(content):
    """Check if content looks like a traditional email (has From: or Subject: headers)."""
    return bool(re.match(r"^(From|Subject):", content, re.IGNORECASE | re.MULTILINE))


def _is_mime_message(msg) -> bool:
    """True only for a genuinely multipart MIME message — never for a plain .txt object
    that merely happens to start with From:/Subject: header-shaped lines (preserves the
    legacy text-parsing path, TODO 2 rule 5)."""
    return bool(msg.is_multipart() or (msg.get_content_type() or "").startswith("multipart/"))


def parse_eml(msg):
    """Extract the plain-text body and each attachment from a parsed MIME message.

    Returns (body_text, attachments). Every attachment is included — oversized,
    non-text, or unreadable ones carry a `skipped_reason` and `text: None` rather
    than being dropped or guessed at (TODO 2 rules 2 & 4).
    """
    body_part = msg.get_body(preferencelist=("plain",))
    body_text = body_part.get_content() if body_part is not None else ""

    attachments = []
    for index, part in enumerate(msg.iter_attachments()):
        filename = part.get_filename() or f"attachment-{index + 1}"
        content_type = part.get_content_type()
        text = None
        skipped_reason = None

        if index >= MAX_ATTACHMENTS:
            skipped_reason = f"exceeds the maximum of {MAX_ATTACHMENTS} attachments per email"
        else:
            payload_bytes = part.get_payload(decode=True) or b""
            if len(payload_bytes) > MAX_ATTACHMENT_BYTES:
                skipped_reason = (
                    f"attachment exceeds the {MAX_ATTACHMENT_BYTES} byte limit "
                    f"({len(payload_bytes)} bytes)"
                )
            elif not content_type.startswith("text/"):
                skipped_reason = f"unsupported content type: {content_type}"
            else:
                try:
                    text = payload_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    skipped_reason = f"attachment is not valid UTF-8 text: {exc}"

        attachments.append(
            {
                "filename": filename,
                "content_type": content_type,
                "text": text,
                "skipped_reason": skipped_reason,
            }
        )

    return body_text, attachments


def _from_address(msg) -> str:
    raw_from = msg.get("From")
    if not raw_from:
        return ""
    _, addr = email.utils.parseaddr(str(raw_from))
    return addr or ""


def handler(event, context):
    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name", "")
    key = detail.get("object", {}).get("key", "")

    if not bucket or not key:
        return {"statusCode": 400, "body": "Missing S3 event details"}

    obj = s3.get_object(Bucket=bucket, Key=key)
    raw_bytes = obj["Body"].read()

    attachments = []
    mime_msg = None
    try:
        candidate = email.message_from_bytes(raw_bytes, policy=email.policy.default)
        if _is_mime_message(candidate):
            mime_msg = candidate
    except Exception as exc:  # noqa: BLE001 — a malformed MIME object falls back to legacy text parsing
        logger.warning("Object %s did not parse as MIME (%s); trying legacy text parsing.", key, exc)

    mime_claimant_email = ""
    if mime_msg is not None:
        try:
            content, attachments = parse_eml(mime_msg)
        except Exception as exc:  # noqa: BLE001 — surface a clear, handled failure, not a crash
            logger.error("Failed to parse MIME attachments for %s: %s", key, exc)
            return {
                "statusCode": 422,
                "body": json.dumps({"error": f"could not parse MIME email: {exc}", "source": f"s3://{bucket}/{key}"}),
            }
        mime_claimant_email = _from_address(mime_msg)
    else:
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            # Genuinely binary, non-MIME object: a clear, logged, handled failure instead of
            # an unhandled UnicodeDecodeError (TODO 2 rule 6). Not retried — decoding will
            # never succeed on a re-delivery of the same bytes.
            logger.error("Object %s is not valid UTF-8 text and not a parseable MIME message: %s", key, exc)
            return {
                "statusCode": 422,
                "body": json.dumps(
                    {"error": f"unreadable object (not UTF-8 text, not MIME): {exc}", "source": f"s3://{bucket}/{key}"}
                ),
            }

    # Determine format and extract claim info
    if is_hesta_form_format(content):
        fields = parse_hesta_form(content)
        prompt = f"Process this HESTA member enquiry:\n\nMember: {fields.get('name', 'Unknown')}\nMember Number: {fields.get('member-number', '')}\nPhone: {fields.get('phone', '')}\nEnquiry Type: {fields.get('reason-for-enquiry', '')}\n\nMessage: {fields.get('message', '')}"
        claimant_email = mime_claimant_email or fields.get("email-address", "")
        source = f"hesta-form:{fields.get('member-number', 'unknown')}"
    elif is_email_format(content):
        headers, body = parse_email(content)
        prompt = f"Process this insurance claim from email:\n\n{body}"
        claimant_email = mime_claimant_email or headers.get("from", "")
        source = f"email:{headers.get('subject', 'No Subject')}"
    elif mime_msg is not None:
        # A MIME message whose text/plain body doesn't itself look like a legacy shape —
        # headers already came off the MIME envelope, so the body IS the member's message.
        prompt = content
        claimant_email = mime_claimant_email
        source = f"email:{mime_msg.get('Subject', 'No Subject')}"
    else:
        try:
            claim_data = json.loads(content)
            prompt = f"Process this claim: {content}"
            claimant_email = claim_data.get("claimant_email", "")
            source = f"s3://{bucket}/{key}"
        except json.JSONDecodeError:
            prompt = content
            claimant_email = ""
            source = f"s3://{bucket}/{key}"

    etag = obj.get("ETag", "").strip('"')
    payload = {
        "prompt": prompt,
        "source": source,
        "source_object_id": f"s3://{bucket}/{key}:{etag}" if etag else f"s3://{bucket}/{key}",
        "idempotency_key": f"s3:{bucket}:{key}:{etag}" if etag else f"s3:{bucket}:{key}",
        "attachments": attachments,
    }
    if claimant_email:
        payload["claimant_email"] = claimant_email

    # Fire-and-forget: invoke Runtime and confirm it accepted the request.
    # The agent processes asynchronously — results go to DynamoDB via tool calls.
    status, preview = invoke_runtime_async(payload)

    logger.info(
        "Runtime accepted claim from %s (HTTP %d). Preview: %s",
        key,
        status,
        " | ".join(preview[:3]),
    )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "Claim submitted for processing",
                "source": f"s3://{bucket}/{key}",
                "runtime_status": status,
            }
        ),
    }
