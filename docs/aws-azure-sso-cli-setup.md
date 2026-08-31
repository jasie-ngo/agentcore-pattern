# AWS CLI Login via Azure AD (Entra) SSO — Working Guide

How to authenticate the AWS CLI on this Mac using Cognizant's Microsoft Entra ID
(Azure AD) SSO — the same login used at `myapps.microsoft.com`. No long-lived
access key / secret is used; the tool issues **temporary** credentials.

> Context: this account uses **SAML federation** (you log in as a *federated user*,
> assuming the role `cloudboost_account_operator`). Long-term access keys can only
> be created for IAM *users*, which you don't have — so temporary SSO credentials
> are the correct and only supported path.

---

## Environment (confirmed working)

- macOS (Apple Silicon), zsh
- AWS CLI v2 (`aws --version` → `aws-cli/2.31.x`)
- Node.js v24 + npm (`node --version`, `npm --version`)
- Tool: **`aws-azure-login`**

## Account details

| Item | Value |
|---|---|
| Account ID | `975050098174` (aka `9750-5009-8174`) |
| Account name | `cb3263443a-apjsandbox` |
| Region | `ap-southeast-2` (Sydney) |
| Assumed role | `cloudboost_account_operator` |
| Azure Tenant ID | `de08c407-19b9-427d-9fe8-edf254300ca7` |
| Azure App ID URI | `10d1e685-cfaa-4bf2-99a0-f3463bb24952` |
| Username | `<employee_id(number)>@cognizant.com` |
| CLI profile | `cognizant-sandbox` |

---

## One-time setup

### 1. Install the tool
```bash
npm install -g aws-azure-login
```
> If it fails on Node 24, use the maintained Go port instead:
> https://github.com/luneo7/go-aws-azure-login

### 2. Configure the profile
```bash
aws-azure-login --configure --profile cognizant-sandbox
```
Answer the prompts with the values from the table above:

- Azure Tenant ID → `de08c407-19b9-427d-9fe8-edf254300ca7`
- Azure App ID URI → `10d1e685-cfaa-4bf2-99a0-f3463bb24952`
- Default Username → `2279521@cognizant.com`
- Default Role ARN → *(leave blank — pick role at login)*
- Duration hours → `12`

This writes settings to `~/.aws/config` under `[profile cognizant-sandbox]`.

### 3. Set the profile as the session default (recommended)
So you never have to type `--profile`:
```bash
echo 'export AWS_PROFILE=cognizant-sandbox' >> ~/.zshrc
source ~/.zshrc
```

---

## Everyday use

### Log in (start of day, or when credentials expire)
```bash
aws-azure-login --profile cognizant-sandbox
```
If MFA / Conditional Access blocks the headless login, open a real browser window:
```bash
aws-azure-login --profile cognizant-sandbox --mode=gui
```

### Verify
```bash
aws sts get-caller-identity --profile cognizant-sandbox
```
Expected:
```json
{
  "UserId": "AROA...:2279521@cognizant.com",
  "Account": "975050098174",
  "Arn": "arn:aws:sts::975050098174:assumed-role/cloudboost_account_operator/2279521@cognizant.com"
}
```

### Use the CLI
```bash
aws s3 ls                      # works if AWS_PROFILE is set
aws s3 ls --profile cognizant-sandbox   # otherwise pass it explicitly
```

---

## Troubleshooting

**"Unable to locate credentials" even though `~/.aws` has files**
- The credentials are stored under the **`cognizant-sandbox`** profile, not
  `default`. A bare `aws ...` command uses `default` and finds nothing.
- Fix: set `export AWS_PROFILE=cognizant-sandbox`, or add `--profile cognizant-sandbox`.

**Credentials expired (auth errors after ~12h)**
- Just re-run: `aws-azure-login --profile cognizant-sandbox`

**`~/.aws/ecf-credentials`**
- Ignore it. The AWS CLI only reads `~/.aws/credentials` and `~/.aws/config`.
  `ecf-credentials` is an unrelated legacy file and is never used.

**Where things live**
- `~/.aws/config` → profile settings (tenant, app id, region) — no secrets
- `~/.aws/credentials` → temporary keys written by `aws-azure-login` (rotated on each login)

---

## Key facts to remember

- This is **temporary-credential** auth via Azure AD SAML federation — there is **no
  permanent access key/secret**, and you cannot create one for a federated identity.
- Credentials last ~12h, then re-run `aws-azure-login --profile cognizant-sandbox`.
- The one value that may need re-fetching if the tenant changes it is the **App ID URI**;
  get it from your cloud/IT team or by scraping the SAML app from `myapps.microsoft.com`.

---

# Working Behind Zscaler (corporate TLS interception)

This machine sits behind **Zscaler**, which intercepts TLS with its own root CA
("Zscaler Root CA", in the macOS System keychain). Anything that verifies TLS
against its *own* bundle instead of the OS keychain fails with:

```
CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate   (Python)
x509: certificate signed by unknown authority                        (Docker / Go)
SELF_SIGNED_CERT_IN_CHAIN / unable to get local issuer certificate   (Node / npm)
```
Fix

Export the Zscaler root CA from the macOS System Keychain and append it to the runtime's trusted CA bundle:
```bash
# Export the Zscaler root certificate
security find-certificate -a -c "Zscaler" -p /Library/Keychains/System.keychain > ~/zscaler-root.pem

# Create a combined certificate bundle
cat "$(python3 -m certifi)" ~/zscaler-root.pem > ~/ca-bundle-zscaler.pem

# Configure runtimes to use the custom bundle
export SSL_CERT_FILE="$HOME/ca-bundle-zscaler.pem"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
export AWS_CA_BUNDLE="$SSL_CERT_FILE"

```
This creates a custom CA bundle containing both the standard trusted certificates and the Zscaler root CA, enabling Python, AWS SDKs, Node.js, Docker, and other runtimes that maintain their own trust stores to successfully establish TLS connections behind Zscaler.

## Python / httpx (Strands MCP clients, etc.)

Use **`truststore`** to verify against the macOS keychain. Scope it to the client
that needs it — do **not** call the global `truststore.inject_into_ssl()`, because
it makes **botocore** recurse infinitely (`RecursionError` in
`create_urllib3_context`). Build the httpx client with a per-client context:

```python
import ssl, httpx, truststore
ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
httpx.AsyncClient(verify=ctx, ...)
```

Note: AWS endpoints (`*.amazonaws.com`, incl. Bedrock) are **not** intercepted by
Zscaler, so botocore/boto3 work fine with their default `certifi` bundle — no CA
change needed there.

## Docker / Colima

The Docker daemon runs inside the Colima **Linux VM**, which has its own CA store.
Install the Zscaler CA into the VM (one-time; survives `colima stop/start`, lost
only on `colima delete`):

```bash
cat ~/zscaler-root.pem | colima ssh -- sudo sh -c 'cat > /usr/local/share/ca-certificates/zscaler-root.crt'
colima ssh -- sudo update-ca-certificates
colima ssh -- sudo systemctl restart docker
docker run --rm hello-world   # verify: prints "Hello from Docker!"
```

## Node / npm (if needed)

npm reaches `registry.npmjs.org` fine in practice, but if a Node tool hits a cert
error, point it at the exported CA:
```bash
export NODE_EXTRA_CA_CERTS=~/zscaler-root.pem
```

> Not everything blocked by Zscaler is a cert problem. A **403 with an HTML page
> and `server: Zscaler`** is a *category block* (e.g. `mcp.exa.ai` → "Generative AI
> and ML Applications"), not TLS. Fix via a ServiceNow request: **Security
> Exception → CS_Corporate Security Service → Unblock Specific URLs (Zscaler)**.

---

# Container Runtime (Colima)

Docker Desktop isn't installed (licensing). Use **Colima** (open-source, CLI-only)
+ the `docker` CLI — it provides the `docker` command that AgentCore `deploy.sh`
and CDK expect.

```bash
brew install colima docker
colima start --cpu 4 --memory 8 --disk 60
```

- After a reboot, run `colima start` again (VM + the Zscaler cert persist).
- To auto-start at login: `brew services start colima`.
- If `docker` errors with `docker-credential-desktop ... not found`, remove the
  stale `"credsStore": "desktop"` line from `~/.docker/config.json`.

---

# AgentCore CLI

- Correct npm package: **`@aws/agentcore`** (provides the `agentcore` command).
  The name `@aws/bedrock-agentcore-cli` in some sample READMEs is **wrong** (npm 404).
- Update: `npm install -g @aws/agentcore@latest` (or `agentcore update`).
- Local dev server: `agentcore dev` (hot-reload). Restart it after adding a new
  dependency — a running dev server won't pick up newly installed packages.

---

# Bedrock Model IDs for ap-southeast-2

Sample defaults often use `us.*` cross-region inference profiles, which **do not
route** in ap-southeast-2. Use `global.*` (or `au.*`) instead. Verified ACTIVE and
invoke-tested in this account/region:

| Purpose | Model ID |
|---|---|
| Primary (Sonnet) | `global.anthropic.claude-sonnet-4-6` |
| Fast (Haiku) | `global.anthropic.claude-haiku-4-5-20251001-v1:0` |

Check what's available:
```bash
aws bedrock list-inference-profiles --region ap-southeast-2 \
  --query "inferenceProfileSummaries[].[inferenceProfileId,status]" --output text
```

---

# Prereqs for running AgentCore samples (e.g. event-driven-claims-agent)

| Requirement | Install / note |
|---|---|
| AWS CLI v2 | present |
| AWS creds | `aws-azure-login --profile cognizant-sandbox` (temporary, ~12h) |
| AgentCore CLI | `@aws/agentcore` (present) |
| AWS CDK | `npm install -g aws-cdk` |
| Container runtime | Colima (see above) + Zscaler CA in the VM |
| uv | `brew install uv` (present) |
| Bedrock access | Sonnet 4.6 + Haiku 4.5 enabled in ap-southeast-2 (verified) |

Deploy to **ap-southeast-2** (not the READMEs' `us-west-2`) and set the model IDs
above. `deploy.sh` provisions ~76 AWS resources (~$3–5/day) and runs an interactive
Cognito step; sandbox SCPs may block some resources (Cognito/SES/IAM) — watch for
`AccessDenied`. Tear down with `./scripts/destroy.sh ap-southeast-2` when done.
