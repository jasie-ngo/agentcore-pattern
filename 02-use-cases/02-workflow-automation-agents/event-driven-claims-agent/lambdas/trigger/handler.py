"""Trigger Lambda: S3 email → EventBridge → Invoke Agent Runtime (fire-and-forget).

The Runtime uses IAM (SigV4) authentication. This Lambda's execution role has
bedrock-agentcore:InvokeAgentRuntime permission granted by CDK.

The invocation is fire-and-forget: the Lambda sends the signed HTTPS request
and confirms the Runtime accepted it (HTTP 200), but does NOT wait for the full
streaming response. The agent processes the claim asynchronously — results are
written to DynamoDB by the agent's tool calls, not returned to this Lambda.
"""

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
REGION = os.environ.get("AWS_REGION", "us-west-2")


def invoke_runtime_async(payload_dict):
    """Invoke the AgentCore Runtime via HTTPS with SigV4 auth (fire-and-forget).

    Sends the request and reads only the first chunk to confirm acceptance.
    Does NOT buffer the full streaming response — the agent processes
    asynchronously and writes results to DynamoDB via tool calls.
    """
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


_HEADER_LINE = re.compile(
    r"^(From|Subject|Date|To|Message-ID|In-Reply-To|References):[ \t]*(.*)$",
    re.IGNORECASE,
)


def parse_email(content):
    """Parse email-format text into structured fields.

    Headers are attacker-controlled, so they are only read, never evaluated.
    Names match case-insensitively. A line starting with a space or tab folds
    into the previous header (RFC 5322 section 2.2.3). If a header appears more
    than once the FIRST occurrence wins, and any folded lines after a repeat are
    discarded rather than appended to the first. A header with no value is left
    out. The blank-line test runs before the fold test, so a folded line can
    never swallow the blank line that ends the headers.
    """
    headers = {}
    lines = content.split("\n")
    body_start = 0  # unchanged: with no blank line the whole text is the body
    current = None  # the header name a folded line would continue, or None
    for i, line in enumerate(lines):
        if line.strip() == "":
            body_start = i + 1
            break
        if line[0] in " \t":
            if current is not None:
                headers[current] = f"{headers[current]} {line.strip()}".strip()
            continue
        match = _HEADER_LINE.match(line.rstrip("\r"))
        if match is None:
            current = None
            continue
        name = match.group(1).lower()
        if name in headers:
            current = None  # first wins; ignore this repeat and its folded lines
            continue
        headers[name] = match.group(2).strip()
        current = name
    body = "\n".join(lines[body_start:]).strip()
    return {k: v for k, v in headers.items() if v}, body


def is_email_format(content):
    """Check if content looks like an email (has From: or Subject: headers)."""
    return bool(re.match(r"^(From|Subject):", content, re.IGNORECASE | re.MULTILINE))


def handler(event, context):
    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name", "")
    key = detail.get("object", {}).get("key", "")

    if not bucket or not key:
        return {"statusCode": 400, "body": "Missing S3 event details"}

    obj = s3.get_object(Bucket=bucket, Key=key)
    content = obj["Body"].read().decode("utf-8")

    # Determine format and extract claim info
    email_headers = None  # set only for email-format input
    if is_email_format(content):
        headers, body = parse_email(content)
        email_headers = headers
        prompt = f"Process this insurance claim from email:\n\n{body}"
        claimant_email = headers.get("from", "")
        source = f"email:{headers.get('subject', 'No Subject')}"
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

    payload = {"prompt": prompt, "source": source}
    if email_headers is not None:
        # Email-format input only; JSON and plain-text payloads stay as they were.
        # Values are forwarded as plain strings, unevaluated.
        payload["headers"] = {
            "message_id": email_headers.get("message-id", ""),
            "in_reply_to": email_headers.get("in-reply-to", ""),
            "references": email_headers.get("references", "").split(),
            "subject": email_headers.get("subject", ""),
            "from": claimant_email,
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
