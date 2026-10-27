"""Trigger Lambda: S3 upload → EventBridge → normalise → Invoke Agent Runtime (fire-and-forget).

The framework's front door, shared by every use case:

1. Works out the use case from the object key — ``claims-inbox/<use_case>/<file>``;
   a key without a known use-case folder goes to the deployed default use case.
2. Normalises the message with ``ingestion.py`` (``app/_shared/ingestion.py``) and
   that use case's ingestion settings (``app/<use_case>/ingestion.yaml``). CDK
   bundles both into this Lambda at build time (``ingestion_configs.json``).
3. Sends the resulting envelope to the Runtime as the ``prompt`` (JSON). Only
   ``prompt`` reaches the agent, so sender, subject and source travel inside it.

The Runtime uses IAM (SigV4) authentication. This Lambda's execution role has
bedrock-agentcore:InvokeAgentRuntime permission granted by CDK.

The invocation is fire-and-forget: the Lambda sends the signed HTTPS request
and confirms the Runtime accepted it (HTTP 200), but does NOT wait for the full
streaming response. The agent processes the message asynchronously — results
are written to DynamoDB by the agent's tool calls, not returned to this Lambda.
"""

import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session as BotocoreSession

_HERE = Path(__file__).resolve().parent
try:  # deployed: CDK bundles ingestion.py next to this file
    from ingestion import normalize
except ImportError:  # running from the repo (unit tests, local tooling)
    sys.path.insert(0, str(_HERE.parents[1] / "app" / "_shared"))
    from ingestion import normalize

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")

# Environment variables (set by CDK)
RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-west-2")
INBOX_PREFIX = "claims-inbox/"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _load_ingestion_configs() -> dict:
    """Bundled settings when deployed; the generated manifest when run from the repo."""
    bundled = _HERE / "ingestion_configs.json"
    if bundled.is_file():
        return json.loads(bundled.read_text(encoding="utf-8"))
    manifest = _HERE.parents[1] / "agentcore" / "generated" / "manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return {"default_use_case": data["default_use_case"], "use_cases": data["ingestion"]}
    return {"default_use_case": "", "use_cases": {}}


INGESTION = _load_ingestion_configs()


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


def is_email_format(content):
    """Check if content looks like an email (has From: or Subject: headers)."""
    return bool(re.match(r"^(From|Subject):", content, re.IGNORECASE | re.MULTILINE))


def use_case_for_key(key: str) -> str:
    """``claims-inbox/<use_case>/<file>`` → use_case; otherwise the deployed default."""
    rest = key.removeprefix(INBOX_PREFIX)
    folder = rest.split("/", 1)[0] if "/" in rest else ""
    return folder if folder in INGESTION["use_cases"] else INGESTION["default_use_case"]


def _address(header_value: str) -> str | None:
    """'Jane Doe <jane@example.com>' → 'jane@example.com'."""
    m = _EMAIL_RE.search(header_value or "")
    return m.group(0) if m else None


def build_envelope(content: str, bucket: str, key: str) -> dict:
    """Normalise one uploaded object into the framework envelope."""
    use_case = use_case_for_key(key)
    config = INGESTION["use_cases"].get(use_case, {})
    sender = subject = None
    body = content

    if is_email_format(content):
        headers, body = parse_email(content)
        sender = _address(headers.get("from", ""))
        subject = headers.get("subject")
    else:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            sender = data.get("claimant_email") or data.get("sender_email")
            # Sample payloads wrap the message in {"prompt": ...}; claim files are the message.
            if isinstance(data.get("prompt"), str):
                body = data["prompt"]

    return normalize(
        body,
        config,
        use_case=use_case,
        sender_email=sender,
        subject=subject,
        source=f"s3://{bucket}/{key}",
    )


def handler(event, context):
    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name", "")
    key = detail.get("object", {}).get("key", "")

    if not bucket or not key:
        return {"statusCode": 400, "body": "Missing S3 event details"}

    obj = s3.get_object(Bucket=bucket, Key=key)
    content = obj["Body"].read().decode("utf-8")

    envelope = build_envelope(content, bucket, key)

    # Fire-and-forget: invoke Runtime and confirm it accepted the request.
    # The agent processes asynchronously — results go to DynamoDB via tool calls.
    status, preview = invoke_runtime_async({"prompt": json.dumps(envelope)})

    logger.info(
        "Runtime accepted %s message from %s (HTTP %d). Preview: %s",
        envelope["use_case"] or "unrouted",
        key,
        status,
        " | ".join(preview[:3]),
    )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "Submitted for processing",
                "use_case": envelope["use_case"],
                "source": envelope["source"],
                "runtime_status": status,
            }
        ),
    }
