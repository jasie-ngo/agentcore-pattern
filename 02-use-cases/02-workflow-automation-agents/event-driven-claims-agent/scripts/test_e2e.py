#!/usr/bin/env python3
"""End-to-end checks for the HESTA ClaimsAgentV2 runtime.

These checks exercise the V2 pipeline contract: a seeded member follows the full
path, an unknown sender creates an anonymous case and receives an identity-
verification draft, and attachments are acknowledged without auto-send.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session as BotocoreSession

STACK_NAME = "AgentCore-ClaimsAgentV2-dev"


def get_runtime_arn(region: str) -> str:
    cf = boto3.client("cloudformation", region_name=region)
    outputs = cf.describe_stacks(StackName=STACK_NAME)["Stacks"][0]["Outputs"]
    for output in outputs:
        if "RuntimeArn" in output["OutputKey"]:
            return output["OutputValue"]
    raise RuntimeError("RuntimeArn not found in V2 stack outputs")


def invoke_agent(runtime_arn: str, region: str, prompt: str) -> str:
    escaped_arn = urllib.parse.quote(runtime_arn, safe="")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations"
    payload = json.dumps({"prompt": prompt}).encode()
    credentials = BotocoreSession().get_credentials().get_frozen_credentials()
    request = AWSRequest(
        method="POST", url=url, data=payload, headers={"Content-Type": "application/json"}
    )
    SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(request)
    response = urllib.request.urlopen(  # nosec B310 - URL is constructed with HTTPS above
        urllib.request.Request(url, data=payload, headers=dict(request.headers)), timeout=180
    )
    parts = []
    for line in response:
        decoded = line.decode("utf-8").strip()
        if decoded.startswith("data: "):
            chunk = decoded[6:]
            if chunk.startswith('"') and chunk.endswith('"'):
                chunk = json.loads(chunk)
            parts.append(chunk)
    return "".join(parts)


def check(name: str, response: str, indicators: list[str]) -> bool:
    missing = [indicator for indicator in indicators if indicator.lower() not in response.lower()]
    passed = not missing
    print(f"{'PASS' if passed else 'FAIL'}: {name}")
    if missing:
        print(f"  Missing: {', '.join(missing)}")
        print(f"  Response: {response[:500]}")
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description="ClaimsAgentV2 HESTA E2E checks")
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--test", type=int, default=0, help="Run test 1-3, or all")
    args = parser.parse_args()

    runtime_arn = get_runtime_arn(args.region)
    scenarios = [
        (
            1,
            "seeded member full pipeline",
            "Please confirm receipt of my completed Binding Death Nomination form. Member number 60010001. [ATTACHMENT form.pdf]",
            ["member_lookup", "case_lookup_creation", "Draft reply", "Attachments"],
        ),
        (
            2,
            "unknown sender anonymous case path",
            "I need help with my HESTA account but cannot provide my member number yet. Sender email is unknown@example.com.",
            ["identity", "case", "identity verification", "human-in-the-loop"],
        ),
        (
            3,
            "attachment is not auto-sent",
            "Please confirm receipt of my document for member number 60010001. [ATTACHMENT signed-form.pdf]",
            ["Draft reply", "NOT sent", "human review"],
        ),
    ]

    selected = [scenario for scenario in scenarios if args.test in (0, scenario[0])]
    results = []
    for _, name, prompt, indicators in selected:
        results.append(check(name, invoke_agent(runtime_arn, args.region, prompt), indicators))
    print(f"{sum(results)}/{len(results)} checks passed")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
