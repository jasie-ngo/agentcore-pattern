# ADR-0010: Cognito Secret via AgentCore Identity Token Vault

**Status:** Accepted (supersedes earlier CDK injection approach). Registration mechanism
updated 2026-09-04: the credential is now created as a CDK resource
(`OAuth2CredentialProvider`) instead of via the `agentcore add credential` CLI command
described below; see the Update note at the end of this ADR. The underlying decision
(use the Identity token vault, not a manual secret store) is unchanged.  
**Date:** 2025-06-24

## Context

The AgentCore Runtime uses Cognito `client_credentials` to obtain a JWT for authenticating outbound calls to the MCP Gateway. The Cognito client secret must be stored securely and accessible to the Runtime at invocation time.

An earlier version of this sample injected the secret directly as a Runtime environment variable via CDK's `unsafe_unwrap()`. This was simple but exposed the secret in CloudFormation templates and environment variable listings.

## Decision

Register the Cognito client secret in the **AgentCore Identity token vault** using `agentcore add credential`. At runtime, the `@requires_access_token` decorator fetches tokens from the vault, so no secret appears in env vars, CDK templates, or code.

## Reasoning

AgentCore Identity is the purpose-built credential management service for agents. Using it:
- **Eliminates secret exposure**: the client secret lives only in the Secrets Manager-backed token vault, not in CloudFormation or environment variables
- **Demonstrates the production pattern**: `@requires_access_token` is the recommended decorator for Gateway auth, and using Identity shows the full intended workflow
- **Handles token lifecycle**: Identity manages token acquisition, caching, and refresh automatically
- **Stays educational**: the `agentcore add credential` CLI command is one line in the deploy script, keeping the sample approachable

## Alternatives Considered

- **CDK `unsafe_unwrap()` to env var (previous approach):** Simpler to wire up but exposes the secret in CloudFormation templates, `aws cloudformation describe-stacks` output, and console env var listings. Acceptable for a learning sample in a personal account, but contradicts the security posture AgentCore Identity is designed to provide.
- **AWS Secrets Manager (manual):** Production-ready with rotation, but requires custom SDK calls in the Runtime. AgentCore Identity wraps Secrets Manager with agent-aware semantics (workload identity + token vault), so using it directly is redundant.
- **SSM Parameter Store (SecureString):** Simpler than raw Secrets Manager but misses the `@requires_access_token` decorator integration.

## Consequences

- The Runtime receives only the **credential provider name** (`AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER=cognito-gateway-m2m`) as an env var, never the secret itself.
- The `@requires_access_token(provider_name="cognito-gateway-m2m", auth_flow="M2M")` decorator handles the full token lifecycle.
- The Runtime's IAM role needs `bedrock-agentcore:GetResourceOauth2Token` and `secretsmanager:GetSecretValue` permissions on the token vault resources (granted by CDK).
- If the Cognito pool is deleted and recreated, redeploying reconciles the credential provider (see Update below).

## Update (2026-09-04): registration moved from CLI to CDK

The registration mechanism described above (`agentcore add credential`) has been replaced. `agentcore/cdk/lib/cdk-stack.ts` now creates the credential as an `OAuth2CredentialProvider` CDK resource (`AWS::BedrockAgentCore::OAuth2CredentialProvider`), part of the same CloudFormation stack as everything else, rather than a separate imperative CLI step run before `agentcore deploy`.

This closed two gaps in the original approach:
- **Stale discovery URL after a region change:** `agentcore add credential` was idempotent, so redeploying to a new region silently left the credential provider pointing at the old region's Cognito pool (`scripts/fix_credential_region.sh` existed specifically to patch this by hand). The CDK resource's `issuer` property is derived from `COGNITO_DISCOVERY_URL` at synth time, so a normal `agentcore deploy` now reconciles it automatically; the fix script remains only as a faster manual fallback.
- **Plaintext secret in a CLI argument:** the old flow passed the Cognito client secret to `agentcore add credential --client-secret ...` as a plaintext CLI argument. `scripts/setup_cognito.sh` now stores the secret in Secrets Manager and writes only its ARN to `.env` for the deploy path; the CDK resource references it via `SecretValue.secretsManager(arn)`, which CloudFormation resolves through a dynamic reference (`{{resolve:secretsmanager:...}}`) rather than embedding the value in the template. (`.env` still also holds the plaintext value separately, since local `agentcore dev` reads it directly to register its own local workload identity.)

The reasoning in this ADR for using the Identity token vault at all (over a manual Secrets Manager or SSM integration) is unaffected; only how the vault entry gets created changed.
