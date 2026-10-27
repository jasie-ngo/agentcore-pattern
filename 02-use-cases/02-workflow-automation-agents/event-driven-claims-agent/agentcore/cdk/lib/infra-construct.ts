/**
 * InfraConstruct: Supplementary infrastructure for the Claims Agent.
 *
 * Creates resources that the AgentCore CLI cannot manage natively:
 * - Shared DynamoDB tables (Policies, Claims, Reviews) + any tables the use cases declare
 * - S3 bucket (claims email inbox, EventBridge enabled)
 * - Gateway tool Lambdas — one per tool in app/<use_case>/gateway_tools.yaml, built
 *   from a blueprint (lambdas/blueprints/*) or custom code, wired via lambdaArnMap
 * - Trigger Lambda (EventBridge → normalise with app/_shared/ingestion.py → Runtime)
 * - EventBridge rule (S3 PutObject → Trigger)
 * - SNS topic (human review notifications)
 *
 * Tools and ingestion settings come from agentcore/generated/manifest.json, written by
 * scripts/build_tools.py. Synth fails if the YAML forms changed since that build.
 *
 * Cognito is managed externally (scripts/setup_cognito.sh) and its values
 * are passed via environment variables at synth time.
 *
 * Exposes `lambdaArnMap` for the parent stack to patch placeholder ARNs in agentcore.json.
 */

import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda_ from 'aws-cdk-lib/aws-lambda';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as events from 'aws-cdk-lib/aws-events';
import * as eventsTargets from 'aws-cdk-lib/aws-events-targets';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import { Construct } from 'constructs';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

/** Tool/ingestion manifest written by scripts/build_tools.py (see docs/FRAMEWORK.md). */
interface TableRef {
  existing?: string;
  created?: string;
}
interface KeySpec {
  name: string;
  type: string;
}
interface CreatedTableSpec {
  id: string;
  use_case: string;
  key: string;
  partition_key: KeySpec;
  sort_key?: KeySpec | null;
  indexes: { name: string; partition_key: KeySpec; sort_key?: KeySpec }[];
}
interface ToolSpec {
  name: string;
  target: string;
  use_case: string;
  kind: 'blueprint' | 'custom';
  code: string;
  // blueprint tools
  table?: TableRef;
  table_access?: 'read' | 'write';
  tool_config?: Record<string, unknown>;
  notify?: boolean;
  // custom tools
  environment?: Record<string, { table?: TableRef; env?: string; default?: string; value?: string }>;
  table_grants?: { table: TableRef; access: 'read' | 'write' }[];
  permissions?: string[];
}
interface ToolManifest {
  source_hash: string;
  default_use_case: string;
  tables: CreatedTableSpec[];
  tools: ToolSpec[];
  ingestion: Record<string, unknown>;
}

const REBUILD_HINT = 'run: uv run --project app python scripts/build_tools.py';

/** Same hash as scripts/build_tools.py:source_hash() — path + content (CRLF→LF) per file. */
function toolSourceHash(projectRoot: string): string {
  const app = path.join(projectRoot, 'app');
  const files = [path.join(app, 'main.py')];
  for (const entry of fs.readdirSync(app, { withFileTypes: true })) {
    const dir = path.join(app, entry.name);
    if (!entry.isDirectory() || entry.name.startsWith('_') || !fs.existsSync(path.join(dir, 'config.yaml'))) continue;
    for (const name of ['gateway_tools.yaml', 'ingestion.yaml']) {
      if (fs.existsSync(path.join(dir, name))) files.push(path.join(dir, name));
    }
  }
  const rel = (f: string) => path.relative(projectRoot, f).split(path.sep).join('/');
  files.sort((a, b) => (rel(a) < rel(b) ? -1 : rel(a) > rel(b) ? 1 : 0));
  const hash = crypto.createHash('sha256');
  for (const f of files) {
    hash.update(Buffer.concat([Buffer.from(rel(f)), Buffer.from([0])]));
    const content = fs.readFileSync(f).toString('latin1').replace(/\r\n/g, '\n');
    hash.update(Buffer.concat([Buffer.from(content, 'latin1'), Buffer.from([0])]));
  }
  return hash.digest('hex');
}

function loadToolManifest(projectRoot: string): ToolManifest {
  const manifestPath = path.join(projectRoot, 'agentcore', 'generated', 'manifest.json');
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`agentcore/generated/manifest.json is missing — ${REBUILD_HINT}`);
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')) as ToolManifest;
  if (manifest.source_hash !== toolSourceHash(projectRoot)) {
    throw new Error(`app/*/gateway_tools.yaml or ingestion.yaml changed since the last tool build — ${REBUILD_HINT}`);
  }
  return manifest;
}

const pascal = (s: string) =>
  s
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map(w => w[0].toUpperCase() + w.slice(1))
    .join('');
const attrType = (t: string) => (t === 'number' ? dynamodb.AttributeType.NUMBER : dynamodb.AttributeType.STRING);

export interface InfraConstructProps {
  /** Whether to destroy data on stack delete (default: true for dev) */
  destroyOnDelete?: boolean;
}

export class InfraConstruct extends Construct {
  /** Map of gateway target name → Lambda function ARN */
  public readonly lambdaArnMap: Record<string, string>;
  /** Trigger Lambda function (needs Runtime ARN injected after creation) */
  public readonly triggerFn: lambda_.Function;
  /** Cognito OIDC discovery URL — read from env, used for Gateway CUSTOM_JWT authorizer */
  public readonly cognitoDiscoveryUrl: string;
  /** Cognito app client ID — read from env, used for Gateway allowedClients */
  public readonly cognitoClientId: string;
  /** Bedrock Guardrail that denies personal financial advice (attached to the Writer model) */
  public readonly adviceGuardrail: bedrock.CfnGuardrail;

  constructor(scope: Construct, id: string, props: InfraConstructProps = {}) {
    super(scope, id);

    const stack = cdk.Stack.of(this);
    const destroyOnDelete = props.destroyOnDelete ?? true;
    const removalPolicy = destroyOnDelete ? cdk.RemovalPolicy.DESTROY : cdk.RemovalPolicy.RETAIN;

    // Project root: process.cwd() is agentcore/cdk/ → go up 2 levels
    const projectRoot = path.resolve(process.cwd(), '..', '..');

    // ─── DynamoDB Tables ───────────────────────────────────────────

    const policiesTable = new dynamodb.Table(this, 'PoliciesTable', {
      tableName: `ClaimsAgent-${stack.stackName.split('-').pop() || 'dev'}-Policies`,
      partitionKey: { name: 'policy_number', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });

    const claimsTable = new dynamodb.Table(this, 'ClaimsTable', {
      tableName: `ClaimsAgent-${stack.stackName.split('-').pop() || 'dev'}-Claims`,
      partitionKey: { name: 'claim_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });

    // Add GSI for listing claims by status (avoids full table scan)
    claimsTable.addGlobalSecondaryIndex({
      indexName: 'status-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'created_at', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const reviewsTable = new dynamodb.Table(this, 'ReviewsTable', {
      tableName: `ClaimsAgent-${stack.stackName.split('-').pop() || 'dev'}-Reviews`,
      partitionKey: { name: 'review_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });

    // Add GSI for finding reviews by claim_id (avoids full table scan)
    reviewsTable.addGlobalSecondaryIndex({
      indexName: 'claim-id-index',
      partitionKey: { name: 'claim_id', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // ─── SNS Topic (human review alerts) ──────────────────────────

    const reviewTopic = new sns.Topic(this, 'ReviewTopic', {
      topicName: 'ClaimsAgent-HumanReview',
    });

    // ─── S3 Bucket (claims email inbox) ───────────────────────────

    const inboxBucket = new s3.Bucket(this, 'InboxBucket', {
      bucketName: `claims-inbox-${stack.account}-${stack.region}`,
      removalPolicy,
      autoDeleteObjects: destroyOnDelete,
      eventBridgeEnabled: true,
    });

    // ─── Cognito values (externally managed — read from env) ──────
    // Cognito is created by scripts/setup_cognito.sh BEFORE deploy.
    // These values are passed via environment variables at synth time.
    this.cognitoDiscoveryUrl = process.env.COGNITO_DISCOVERY_URL || 'PLACEHOLDER_DISCOVERY_URL';
    this.cognitoClientId = process.env.AGENTCORE_GATEWAY_CLIENT_ID || 'PLACEHOLDER_CLIENT_ID';

    // ─── Gateway tool Lambdas (from app/<use_case>/gateway_tools.yaml) ──
    // One Lambda per tool. Blueprint tools share code (lambdas/blueprints/<type>) and
    // differ only in their settings; custom tools bring their own code folder.

    const lambdasPath = path.join(projectRoot, 'lambdas');
    const manifest = loadToolManifest(projectRoot);
    const existingTables: Record<string, dynamodb.Table> = {
      Policies: policiesTable,
      Claims: claimsTable,
      Reviews: reviewsTable,
    };
    const createdTables: Record<string, dynamodb.Table> = {};
    for (const spec of manifest.tables) {
      const table = new dynamodb.Table(this, `Table${pascal(spec.id)}`, {
        tableName: `${stack.stackName}-${spec.use_case}-${spec.key}`.replace(/_/g, '-'),
        partitionKey: { name: spec.partition_key.name, type: attrType(spec.partition_key.type) },
        sortKey: spec.sort_key ? { name: spec.sort_key.name, type: attrType(spec.sort_key.type) } : undefined,
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        removalPolicy,
      });
      for (const index of spec.indexes) {
        table.addGlobalSecondaryIndex({
          indexName: index.name,
          partitionKey: { name: index.partition_key.name, type: attrType(index.partition_key.type) },
          sortKey: index.sort_key ? { name: index.sort_key.name, type: attrType(index.sort_key.type) } : undefined,
          projectionType: dynamodb.ProjectionType.ALL,
        });
      }
      createdTables[spec.id] = table;
    }
    const tableFor = (ref: TableRef | undefined, tool: string): dynamodb.Table => {
      const table = ref?.existing ? existingTables[ref.existing] : ref?.created ? createdTables[ref.created] : undefined;
      if (!table) throw new Error(`Tool ${tool}: unknown table ${JSON.stringify(ref)} — ${REBUILD_HINT}`);
      return table;
    };
    const grant = (table: dynamodb.Table, fn: lambda_.Function, access: 'read' | 'write') =>
      access === 'write' ? table.grantReadWriteData(fn) : table.grantReadData(fn);

    this.lambdaArnMap = {};
    for (const tool of manifest.tools) {
      const environment: Record<string, string> = {};
      if (tool.kind === 'blueprint') {
        environment.TABLE_NAME = tableFor(tool.table, tool.name).tableName;
        environment.TOOL_CONFIG = JSON.stringify(tool.tool_config ?? {});
        if (tool.notify) environment.NOTIFY_TOPIC_ARN = reviewTopic.topicArn;
      } else {
        for (const [name, spec] of Object.entries(tool.environment ?? {})) {
          if (spec.table) environment[name] = tableFor(spec.table, tool.name).tableName;
          else if (spec.env) environment[name] = process.env[spec.env] || spec.default || '';
          else environment[name] = spec.value ?? '';
        }
      }

      const kindLabel = tool.kind === 'blueprint' ? path.basename(tool.code) : 'custom';
      const fn = new lambda_.Function(this, `Tool${pascal(tool.target)}Fn`, {
        functionName: `${stack.stackName}-${tool.target}`.slice(0, 64),
        description: `Gateway tool ${tool.name} (${tool.use_case}, ${kindLabel})`,
        runtime: lambda_.Runtime.PYTHON_3_12,
        handler: 'handler.handler',
        code: lambda_.Code.fromAsset(path.join(projectRoot, tool.code)),
        environment,
        timeout: cdk.Duration.seconds(10),
      });

      if (tool.kind === 'blueprint') {
        grant(tableFor(tool.table, tool.name), fn, tool.table_access ?? 'read');
        if (tool.notify) reviewTopic.grantPublish(fn);
      } else {
        for (const g of tool.table_grants ?? []) grant(tableFor(g.table, tool.name), fn, g.access);
        if (tool.permissions?.includes('ses_send')) {
          fn.addToRolePolicy(
            new iam.PolicyStatement({
              actions: ['ses:SendEmail', 'ses:SendRawEmail'],
              resources: [`arn:aws:ses:${stack.region}:${stack.account}:identity/*`],
            })
          );
        }
      }
      this.lambdaArnMap[tool.target] = fn.functionArn;
    }

    // ─── Dead-Letter Queue (failed claim triggers) ────────────────

    const triggerDlq = new sqs.Queue(this, 'TriggerDLQ', {
      queueName: 'ClaimsAgent-TriggerDLQ',
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Alarm when failed claims land in the DLQ — ensures ops visibility
    new cloudwatch.Alarm(this, 'TriggerDLQAlarm', {
      alarmName: 'ClaimsAgent-FailedClaims',
      alarmDescription: 'Claims trigger DLQ has messages — failed claim processing needs attention',
      metric: triggerDlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.minutes(1),
      }),
      threshold: 1,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // ─── Trigger Lambda (EventBridge → Runtime) ───────────────────

    // The Trigger normalises every upload with the same code the agents use
    // (app/_shared/ingestion.py) and each use case's ingestion settings. Its files are
    // staged together at synth time rather than kept as copies in the repo.
    const triggerStage = fs.mkdtempSync(path.join(os.tmpdir(), 'agentcore-trigger-'));
    fs.copyFileSync(path.join(lambdasPath, 'trigger', 'handler.py'), path.join(triggerStage, 'handler.py'));
    fs.copyFileSync(path.join(projectRoot, 'app', '_shared', 'ingestion.py'), path.join(triggerStage, 'ingestion.py'));
    fs.writeFileSync(
      path.join(triggerStage, 'ingestion_configs.json'),
      JSON.stringify({ default_use_case: manifest.default_use_case, use_cases: manifest.ingestion }, null, 2)
    );

    this.triggerFn = new lambda_.Function(this, 'TriggerFn', {
      functionName: 'ClaimsAgent-Trigger',
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(triggerStage),
      environment: {
        // AGENTCORE_RUNTIME_ARN injected by parent stack after Runtime is created
        AGENTCORE_RUNTIME_ARN: 'PENDING',
      },
      timeout: cdk.Duration.seconds(90),
      deadLetterQueue: triggerDlq,
      retryAttempts: 2,
    });
    inboxBucket.grantRead(this.triggerFn);

    // ─── EventBridge Rule: S3 PutObject → Trigger Lambda ──────────

    new events.Rule(this, 'ClaimInboxRule', {
      ruleName: 'ClaimsAgent-InboxTrigger',
      eventPattern: {
        source: ['aws.s3'],
        detailType: ['Object Created'],
        detail: {
          bucket: { name: [inboxBucket.bucketName] },
          object: { key: [{ prefix: 'claims-inbox/' }] },
        },
      },
      targets: [new eventsTargets.LambdaFunction(this.triggerFn)],
    });

    // ─── Bedrock Guardrail: deny personal financial advice ────────
    // Platform control (showcase): a denied-topic guardrail attached to the Writer model
    // so the agent cannot generate personal financial/product advice. Works together with
    // the app-layer detection (AI-001) + routing + Reviewer checks.
    this.adviceGuardrail = new bedrock.CfnGuardrail(this, 'AdviceGuardrail', {
      name: `ClaimsAgent-${stack.stackName.split('-').pop() || 'dev'}-NoPersonalAdvice`,
      description: 'Denies personal financial/investment/product advice; HESTA staff handle advice enquiries.',
      blockedInputMessaging: 'This enquiry needs a HESTA team member — we can’t provide personal financial advice.',
      blockedOutputsMessaging: '[GUARDRAIL_BLOCKED_ADVICE]',
      topicPolicyConfig: {
        topicsConfig: [
          {
            name: 'PersonalFinancialAdvice',
            type: 'DENY',
            definition:
              'Personal financial or investment advice, or product/option recommendations, tailored to an '
              + "individual member's own circumstances (what they personally should do with their super).",
            // Bedrock allows at most 5 examples per topic.
            examples: [
              'Which super option is best for me?',
              'Should I switch to the high growth option?',
              'What should I invest my super in?',
              'Is it a good idea for me to roll over my other fund for my situation?',
              'Can you recommend the best investment choice for me?',
            ],
          },
        ],
      },
    });

    // ─── Outputs ──────────────────────────────────────────────────

    new cdk.CfnOutput(this, 'InboxBucketName', { value: inboxBucket.bucketName });
    new cdk.CfnOutput(this, 'ReviewTopicArn', { value: reviewTopic.topicArn });
    new cdk.CfnOutput(this, 'TriggerDLQUrl', { value: triggerDlq.queueUrl });
    new cdk.CfnOutput(this, 'AdviceGuardrailId', { value: this.adviceGuardrail.attrGuardrailId });
  }
}
