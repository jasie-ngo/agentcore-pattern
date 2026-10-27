# Event-Driven Claims Agent — Documentation

The Event-Driven Claims Agent is an insurance claims processor built on Amazon Bedrock AgentCore. It demonstrates a dual-agent architecture where a Claims Processor evaluates submissions, a Validation Agent reviews decisions, and the system routes outcomes to auto-approval or human review based on confidence scoring.

## Where to Start

| Your goal | Start here |
|-----------|-----------|
| Deploy and try it out | [README Quick Start](../README.md#quick-start) |
| Understand how it works | [Framework](FRAMEWORK.md), then [Architecture](ARCHITECTURE.md) |
| Switch or add a use case | [Switching Use Cases](../SWITCHING_USE_CASES.md) |
| Look up a specific setting | [Configuration Reference](CONFIGURATION.md) |
| Validate auth & behavior after deploy | [`scripts/test_auth.py`](../scripts/test_auth.py) + [Deployment → Verify](deployment.md#9-verify-deployment) |
| Deploy step-by-step (manual) | [Deployment Guide](deployment.md) |

## Prerequisites

- AWS Account with Bedrock model access (Claude Sonnet 4)
- AgentCore CLI (`agentcore --version` ≥ 1.0.0-preview.13)
- Node.js 18+ (for CDK)
- Docker or Finch (for container builds)
- Python 3.12+ with uv
- AWS CLI v2 configured

## Documentation Index

| Document | Description |
|----------|-------------|
| [Framework](FRAMEWORK.md) | Use-case folders, the inbound envelope, blueprint and custom Gateway tools, the build step |
| [Architecture](ARCHITECTURE.md) | System components, data flows, dual-agent pipeline, and Mermaid diagrams |
| [Deployment](deployment.md) | One-command deploy, manual step-by-step, local dev, verification, and teardown |
| [Configuration](CONFIGURATION.md) | Every env var, Cedar policy, Cognito setting, model config, and memory parameter |
