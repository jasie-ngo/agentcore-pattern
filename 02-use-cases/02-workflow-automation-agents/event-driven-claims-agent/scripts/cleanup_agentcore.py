#!/usr/bin/env python3
"""Event-Driven Claims Agent — Full Resource Cleanup.

Handles the complete teardown lifecycle:
1. Delete CloudFormation stack (with DELETE_FAILED auto-recovery)
2. Delete orphaned AgentCore control-plane resources
3. Delete Cognito User Pool (if script-created)
4. Clean local state files

Usage:
    python3 scripts/cleanup_agentcore.py --region us-east-1 --project-dir .
    python3 scripts/cleanup_agentcore.py --region ap-southeast-2 --client acme [--sweep-orphans]

With no --client the original single-client names are used, exactly as before.  With one, the stack, credential provider and
Cognito state are that client's own (see scripts/clientctx.py), and the sweep of orphaned AgentCore resources is OFF unless
--sweep-orphans is given, because the sweep identifies resources by name alone and an account may hold several clients.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import boto3
from botocore.exceptions import ClientError

import clientctx

# Set by configure() from the resolved deployment.  The defaults are the original single-client names.
STACK_NAME = "AgentCore-ClaimsAgent-dev"
PROJECT_PREFIX = "ClaimsAgent"
CREDENTIAL_NAME = "cognito-gateway-m2m"
CTX = None
SIBLINGS: set = set()


def configure(ctx) -> None:
    """Point the module's names at one deployment."""
    global STACK_NAME, PROJECT_PREFIX, CREDENTIAL_NAME, CTX, SIBLINGS
    CTX = ctx
    STACK_NAME = ctx.stack_name
    PROJECT_PREFIX = ctx.project
    CREDENTIAL_NAME = ctx.credential
    SIBLINGS = clientctx.known_projects()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a shell command, return result (never raises)."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def cfn_client(region: str):
    return boto3.client("cloudformation", region_name=region)


def agentcore_client(region: str):
    return boto3.client("bedrock-agentcore-control", region_name=region)


def matches_project(name: str) -> bool:
    """Check if a resource name belongs to this deployment.

    The original deployment keeps its loose test; a client is matched as a whole word and never as another client's longer name
    (clientctx.owns_resource)."""
    if CTX is None:  # used before configure(): the original behaviour
        lower = name.lower()
        return "claimsagent" in lower or "claims" in lower
    return clientctx.owns_resource(name, CTX, SIBLINGS)


# ─── Step 1: CloudFormation Stack ─────────────────────────────────────────────


def delete_stack(region: str):
    """Delete the CloudFormation stack, handling DELETE_FAILED gracefully."""
    cfn = cfn_client(region)

    # Check current status
    try:
        resp = cfn.describe_stacks(StackName=STACK_NAME)
        status = resp["Stacks"][0]["StackStatus"]
    except ClientError:
        print("   Stack does not exist — skipping.")
        return

    print(f"   Stack status: {status}")

    if status == "DELETE_COMPLETE":
        print("   Already deleted.")
        return

    # If already failed, retry with retained resources
    if status == "DELETE_FAILED":
        _retry_with_retain(cfn, region)
        return

    # Initiate delete
    if status not in ("DELETE_IN_PROGRESS",):
        print("   Initiating stack deletion...")
        cfn.delete_stack(StackName=STACK_NAME)

    # Wait (up to 5 minutes)
    print("   Waiting for deletion (up to 5 min)...")
    for _ in range(60):
        time.sleep(5)
        try:
            resp = cfn.describe_stacks(StackName=STACK_NAME)
            status = resp["Stacks"][0]["StackStatus"]
        except ClientError:
            print("   ✓ Stack deleted.")
            return

        if status == "DELETE_COMPLETE":
            print("   ✓ Stack deleted.")
            return
        if status == "DELETE_FAILED":
            _retry_with_retain(cfn, region)
            return

    print("   ⚠️  Timed out waiting. Proceeding with cleanup anyway.")


def _retry_with_retain(cfn, region: str):
    """Handle DELETE_FAILED by retaining problematic resources and retrying."""
    print("   Stack in DELETE_FAILED — retrying with retained resources...")

    # Find resources that failed to delete
    failed = set()
    paginator = cfn.get_paginator("describe_stack_events")
    for page in paginator.paginate(StackName=STACK_NAME):
        for event in page["StackEvents"]:
            if event.get("ResourceStatus") == "DELETE_FAILED" and event.get("LogicalResourceId") != STACK_NAME:
                failed.add(event["LogicalResourceId"])

    if failed:
        print(f"   Retaining: {', '.join(failed)}")
        cfn.delete_stack(StackName=STACK_NAME, RetainResources=list(failed))
    else:
        cfn.delete_stack(StackName=STACK_NAME)

    # Wait for completion
    try:
        waiter = cfn.get_waiter("stack_delete_complete")
        waiter.wait(StackName=STACK_NAME, WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        print("   ✓ Stack deleted (retained resources cleaned below).")
    except Exception:
        print("   ⚠️  Stack delete wait failed. Proceeding with cleanup.")


# ─── Step 2: Orphaned AgentCore Resources ─────────────────────────────────────


def cleanup_agentcore_resources(region: str):
    """Delete orphaned AgentCore control-plane resources matching the project."""
    print("\n   Cleaning AgentCore control-plane resources...")

    try:
        client = agentcore_client(region)
    except Exception as e:
        print(f"   ⚠️  Cannot create control client: {e} — skipping.")
        return

    # Gateways (delete targets first)
    for gw in _list_safe(client, "list_gateways", "gateways"):
        if not matches_project(gw.get("name", "")):
            continue
        gw_id = gw["gatewayId"]
        for t in _list_safe(client, "list_gateway_targets", "targets", gatewayIdentifier=gw_id):
            _delete_safe(
                client,
                "delete_gateway_target",
                gatewayIdentifier=gw_id,
                targetId=t["targetId"],
            )
        time.sleep(1)
        _delete_safe(client, "delete_gateway", gatewayIdentifier=gw_id)
        print(f"   Deleted gateway: {gw.get('name')}")

    # Policy Engines (delete policies first)
    for pe in _list_safe(client, "list_policy_engines", "policyEngineSummaries"):
        if not matches_project(pe.get("name", "")):
            continue
        pe_id = pe["policyEngineId"]
        for pol in _list_safe(client, "list_policies", "policySummaries", policyEngineId=pe_id):
            _delete_safe(client, "delete_policy", policyEngineId=pe_id, policyId=pol["policyId"])
        time.sleep(1)
        _delete_safe(client, "delete_policy_engine", policyEngineId=pe_id)
        print(f"   Deleted policy engine: {pe.get('name')}")

    # Runtimes (delete non-DEFAULT endpoints first)
    for rt in _list_safe(client, "list_agent_runtimes", "agentRuntimeSummaries"):
        if not matches_project(rt.get("name", "")):
            continue
        rt_id = rt["agentRuntimeId"]
        for ep in _list_safe(
            client,
            "list_agent_runtime_endpoints",
            "agentRuntimeEndpointSummaries",
            agentRuntimeId=rt_id,
        ):
            if ep.get("name") != "DEFAULT":
                _delete_safe(
                    client,
                    "delete_agent_runtime_endpoint",
                    agentRuntimeId=rt_id,
                    endpointName=ep["name"],
                )
        _delete_safe(client, "delete_agent_runtime", agentRuntimeId=rt_id)
        print(f"   Deleted runtime: {rt.get('name')}")

    # Memories
    for mem in _list_safe(client, "list_memories", "memorySummaries"):
        if not matches_project(mem.get("name", "")):
            continue
        _delete_safe(client, "delete_memory", memoryId=mem["memoryId"])
        print(f"   Deleted memory: {mem.get('name')}")

    # Credential provider
    _delete_safe(client, "delete_oauth2_credential_provider", name=CREDENTIAL_NAME)
    print(f"   Deleted credential: {CREDENTIAL_NAME}")

    print("   ✓ AgentCore resource cleanup complete.")


def _list_safe(client, method: str, key: str, **kwargs) -> list:
    """Call a list method, return empty list on failure."""
    try:
        return getattr(client, method)(**kwargs).get(key, [])
    except (ClientError, Exception):
        return []


def _delete_safe(client, method: str, **kwargs):
    """Call a delete method, swallow errors."""
    try:
        getattr(client, method)(**kwargs)
    except (ClientError, Exception):
        pass


# ─── Step 3: Cognito ─────────────────────────────────────────────────────────


def teardown_cognito(project_dir: str):
    """Delete Cognito User Pool if created by setup_cognito.sh."""
    state_file = os.path.join(project_dir, ".cognito-state.json")
    if not os.path.exists(state_file):
        print("\n   Cognito: not script-created — skipping.")
        return

    with open(state_file) as f:
        state = json.load(f)

    region = state["region"]
    pool_id = state["user_pool_id"]
    domain = state.get("domain_prefix", "")

    print(f"\n   Deleting Cognito pool {pool_id} in {region}...")
    cognito = boto3.client("cognito-idp", region_name=region)

    # Delete domain first (required before pool)
    if domain:
        try:
            cognito.delete_user_pool_domain(Domain=domain, UserPoolId=pool_id)
        except ClientError:
            pass

    # Delete pool (cascades clients + resource servers)
    try:
        cognito.delete_user_pool(UserPoolId=pool_id)
        print(f"   ✓ Cognito pool deleted: {pool_id}")
    except ClientError as e:
        print(f"   ⚠️  Cognito delete failed: {e}")

    os.remove(state_file)


# ─── Step 4: Local State ─────────────────────────────────────────────────────


def clean_local_state(project_dir: str):
    """Remove generated state files."""
    print("\n   Cleaning local state...")
    targets = os.path.join(project_dir, "agentcore", "aws-targets.json")
    deployed = os.path.join(project_dir, "agentcore", ".cli", "deployed-state")

    if os.path.exists(targets):
        os.remove(targets)

    # Remove deployed-state directory or file variants
    for pattern in (deployed, f"{deployed}.json", f"{deployed}s"):
        if os.path.exists(pattern):
            if os.path.isdir(pattern):
                shutil.rmtree(pattern)
            else:
                os.remove(pattern)

    print("   ✓ Local state cleaned.")


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Full Claims Agent teardown")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    clientctx.add_arguments(parser)  # --client, --project-dir (default: the deployment's own project), --target
    parser.add_argument(
        "--sweep-orphans",
        action="store_true",
        help="also delete leftover AgentCore resources by name (always on without --client; opt-in for a client)",
    )
    args = parser.parse_args()

    try:
        ctx = clientctx.context_from_args(args)
    except clientctx.CtxError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    configure(ctx)
    project_dir = str(ctx.project_dir)

    print(f"   Region: {args.region}")
    print(f"   Stack:  {STACK_NAME}")
    if ctx.client:
        print(f"   Client: {ctx.client} (project {ctx.project}, directory {project_dir})")
    print("")

    # 1. CloudFormation stack
    print("   ── CloudFormation ──")
    delete_stack(args.region)

    # 2. Orphaned AgentCore resources.  Matching is by name, so a client must ask for it.
    print("\n   ── AgentCore Resources ──")
    if ctx.client and not args.sweep_orphans:
        print(f"   Skipped: the sweep matches by name. Add --sweep-orphans to delete leftovers named {ctx.project}...")
        # The credential provider is exact by name, so it is always removed.
        _delete_safe(agentcore_client(args.region), "delete_oauth2_credential_provider", name=CREDENTIAL_NAME)
        print(f"   Deleted credential: {CREDENTIAL_NAME}")
    else:
        cleanup_agentcore_resources(args.region)

    # 3. Cognito
    print("\n   ── Cognito ──")
    teardown_cognito(project_dir)

    # 4. Local state
    clean_local_state(project_dir)


if __name__ == "__main__":
    sys.exit(main())
