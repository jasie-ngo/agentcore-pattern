# Client assembly

An operator assembles a deployable project for one client from the shared core and that client's folder, then validates it with the AgentCore CLI. Nothing is deployed and AWS is not contacted.

## Sub-features

- `assemble` builds a per-client project with the core, the client package and the client's pack copied in.
- `assemble-validate` shows `agentcore validate` accepting the assembled project.

## How to get to it (user POV)

- `python3 scripts/assemble_client.py <client> --display-name "<Name>" --profile presale --pack-dir clients/<name>/pack`.
- Then `agentcore validate` in the assembled project, after copying `agentcore/aws-targets.json.template` to `aws-targets.json` with an account and region.

## Driving it with control-claims.sh

Preconditions:

- `agentcore` is on `PATH` (`agentcore --version` printed `0.27.1` when this skill was written).
- No server and no AWS credentials are needed.

- **Assemble and validate.** Run `control-claims.sh assemble`. It assembles client `verifyrun` into `.run/<RUN_ID>/assembled` (never `build/`), writes a placeholder `aws-targets.json` with account `000000000000` and region `ap-southeast-2`, and runs `agentcore validate --json`.
- **Assemble result.** `artifacts/<RUN_ID>/assemble.txt` shows `assembled verifyrun -> ...`, `profile presale`, `pack: in image, off`, and exit `0`. The pack is in the image but switched off, which is the default.
- **Validate result.** `artifacts/<RUN_ID>/assemble-validate.txt` holds `{"success":true}` and exit `0`.
- **Look inside.** In the assembled directory, `BUILD-INFO.json`, `README-BUILD.md`, `agentcore/agentcore.json`, `app/04_hesta_member_email/` and `lambdas/` exist. Run this before `control-claims.sh clean`, which removes the assembled directory.
- **Scripts for the client.** Run `control-claims.sh scripts-plan`. It runs `deploy.sh`, `destroy.sh`, `scripts/destroy.sh`, `scripts/setup_cognito.sh`, `scripts/teardown_cognito.sh` and `scripts/fix_credential_region.sh` with `--client verifyrun --plan`, then `deploy.sh --plan` with no client. A fake `aws` that fails if called is first on the `PATH`. Each client block shows `CloudFormation stack  AgentCore-Verifyrun-dev`, `credential provider  verifyrun-gateway-m2m` and `Cognito user pool  Verifyrun-UserPool`; the last block shows the original `AgentCore-ClaimsAgent-dev`, `cognito-gateway-m2m` and `ClaimsAgent-UserPool`. `artifacts/<RUN_ID>/scripts-plan.txt` ends with `# exit_code: 0` and contains no `FAKE aws was called` line.
- **Proof.** `assemble.txt`, `assemble-validate.txt` and `scripts-plan.txt`.

## Gotchas

- `agentcore validate` fails with `Required file not found: aws-targets.json` until the targets file exists. With the template copied unchanged it fails again, because `${CDK_DEFAULT_ACCOUNT}` is not 12 digits. The helper writes a valid placeholder.
- `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER` must be set for validate. The helper sets it to `verifyrun-gateway-m2m`.
- `--enable-pack` is refused unless the pack verifies. Today no pack is approved, so do not use it as a proof path.
- `deploy.sh`, `destroy.sh` and `scripts/` take `--client <slug>`. Verify them only with `--plan`, which prints the names and stops before any AWS call. Never run them without `--plan` as verification, and do not run the assembled project's `agentcore deploy`.
- Validate checks configuration. It says nothing about the container or a deployed stack.
