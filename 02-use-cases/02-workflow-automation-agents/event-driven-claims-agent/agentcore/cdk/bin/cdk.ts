#!/usr/bin/env node
import { AgentCoreStack } from '../lib/cdk-stack';
import { parseClient } from '../lib/naming';
import { resolveGuardrailConfig } from '../lib/guardrail-topics';
import { ConfigIO, type AgentCoreMcpSpec, type AwsDeploymentTarget } from '@aws/agentcore-cdk';
import { App, Aspects, type Environment } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import * as path from 'path';
import * as fs from 'fs';

function toEnvironment(target: AwsDeploymentTarget): Environment {
  return {
    account: target.account,
    region: target.region,
  };
}

/**
 * The `context` block of this project's cdk.json, read directly. The CDK command-line tool injects it into the app, but running
 * the app on its own does not, and the client for a deployment lives there (scripts/assemble_client.py writes it). Reading it
 * here means every way of running the app agrees.
 */
function readCdkJsonContext(): Record<string, unknown> {
  try {
    const parsed = JSON.parse(fs.readFileSync(path.join(process.cwd(), 'cdk.json'), 'utf8'));
    return (parsed.context ?? {}) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function sanitize(name: string): string {
  return name.replace(/_/g, '-');
}

function toStackName(projectName: string, targetName: string): string {
  return `AgentCore-${sanitize(projectName)}-${sanitize(targetName)}`;
}

async function main() {
  // Config root is parent of cdk/ directory. The CLI sets process.cwd() to agentcore/cdk/.
  const configRoot = path.resolve(process.cwd(), '..');
  const configIO = new ConfigIO({ baseDir: configRoot });

  const spec = await configIO.readProjectSpec();
  const targets = await configIO.readAWSDeploymentTargets();

  // Extract MCP/Gateway configuration from the project spec. These fields are
  // now typed on AgentCoreProjectSpec (@aws/agentcore-cdk alpha.39+).
  const mcpSpec: AgentCoreMcpSpec | undefined = spec.agentCoreGateways?.length
    ? {
        agentCoreGateways: spec.agentCoreGateways,
        mcpRuntimeTools: spec.mcpRuntimeTools,
      }
    : undefined;

  // Read deployed state for credential ARNs (populated by pre-deploy identity setup)
  let deployedState: Record<string, unknown> | undefined;
  try {
    deployedState = JSON.parse(fs.readFileSync(path.join(configRoot, '.cli', 'deployed-state.json'), 'utf8'));
  } catch {
    // Deployed state may not exist on first deploy
  }

  if (targets.length === 0) {
    throw new Error('No deployment targets configured. Please define targets in agentcore/aws-targets.json');
  }

  const app = new App();

  // The client this deployment belongs to (ADR-0015 Q7). `-c client=acme` or AGENTCORE_CLIENT. Unset keeps the original shared
  // names, so an existing deployment is unchanged. A malformed slug stops here with a readable message.
  // Precedence: CDK context (-c, or cdk.json via the tool), then the environment, then cdk.json read directly.
  const fileContext = readCdkJsonContext();
  const client = parseClient(
    app.node.tryGetContext('client') ?? process.env.AGENTCORE_CLIENT ?? (fileContext.client as string | undefined)
  );
  const clientDisplayName: string | undefined =
    app.node.tryGetContext('clientName') ??
    (process.env.AGENTCORE_CLIENT_NAME || undefined) ??
    (fileContext.clientName as string | undefined);

  // The guardrail's wording, from the client's file (see lib/guardrail-topics.ts). A named file that is missing or invalid stops the
  // synth here with a readable message, so a typo can never quietly fall back to the default wording.
  const guardrailFile: string | undefined =
    app.node.tryGetContext('guardrailTopics') ??
    (process.env.AGENTCORE_GUARDRAIL_TOPICS || undefined) ??
    (fileContext.guardrailTopics as string | undefined);
  const guardrail = resolveGuardrailConfig(guardrailFile, configRoot).config;

  for (const target of targets) {
    const env = toEnvironment(target);
    const stackName = toStackName(spec.name, target.name);

    // Extract credentials from deployed state for this target
    const targetState = (deployedState as Record<string, unknown>)?.targets as
      | Record<string, Record<string, unknown>>
      | undefined;
    const targetResources = targetState?.[target.name]?.resources as Record<string, unknown> | undefined;
    const credentials = targetResources?.credentials as
      | Record<string, { credentialProviderArn: string; clientSecretArn?: string }>
      | undefined;

    new AgentCoreStack(app, stackName, {
      spec,
      mcpSpec,
      credentials,
      client,
      clientDisplayName,
      guardrail,
      env,
      description: `AgentCore stack for ${spec.name} deployed to ${target.name} (${target.region})`,
      tags: {
        'agentcore:project-name': spec.name,
        'agentcore:target-name': target.name,
        ...(client ? { 'agentcore:client': client } : {}),
      },
    });
  }

  if (process.env.CDK_NAG === "true") {
    Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
  }
  app.synth();
}

main().catch((error: unknown) => {
  console.error('AgentCore CDK synthesis failed:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
