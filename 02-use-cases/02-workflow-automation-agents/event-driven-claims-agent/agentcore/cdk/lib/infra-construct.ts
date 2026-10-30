/**
 * InfraConstruct: Supplementary infrastructure for the Claims Agent.
 *
 * Creates resources that the AgentCore CLI cannot manage natively:
 * - DynamoDB tables (Policies, Claims, Reviews)
 * - S3 bucket (claims email inbox, EventBridge enabled)
 * - Lambda tool functions (6 tools wired to Gateway via lambdaArnMap)
 * - Trigger Lambda (EventBridge → Runtime invocation)
 * - EventBridge rule (S3 PutObject → Trigger)
 * - SNS topic (human review notifications)
 *
 * Cognito is managed externally (scripts/setup_cognito.sh) and its values
 * are passed via environment variables at synth time.
 *
 * Client mode (ADR-0015, Q7): with `client` set, every physical name carries the client slug (see naming.ts), the data stores
 * are encrypted with a key of the client's own, and the guardrail's messages use the client's name. With no client the
 * original names and settings are produced exactly, so an existing deployment is unchanged.
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
import * as kms from 'aws-cdk-lib/aws-kms';
import { Construct } from 'constructs';
import * as path from 'path';
import { ResourceNames, parseClient } from './naming';
import { LEGACY_GUARDRAIL, NEUTRAL_GUARDRAIL, renderGuardrailText, type GuardrailConfig } from './guardrail-topics';

export interface InfraConstructProps {
  /** Whether to destroy data on stack delete (default: true for dev) */
  destroyOnDelete?: boolean;
  /** Client slug. Unset keeps the original shared names and settings, so existing deployments are unchanged. */
  client?: string;
  /** How the client is named in end-user text (the guardrail's messages). Unset in client mode gives generic wording. */
  clientDisplayName?: string;
  /** The guardrail's topics and messages, from the client's file (lib/guardrail-topics.ts). Unset uses the built-in default. */
  guardrail?: GuardrailConfig;
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

    const client = parseClient(props.client);
    const stage = stack.stackName.split('-').pop() || 'dev';
    const names = new ResourceNames(client, stage, stack.account, stack.region);

    // A key of the client's own for the data stores (ADR-0015 Q7, condition 2). Client mode only: it adds a key and changes
    // how the tables, bucket, topic and queue are encrypted, which an existing deployment must not be surprised by.
    // If SES delivers inbound mail straight into the bucket, SES must also be allowed to use this key.
    const dataKey = client
      ? new kms.Key(this, 'ClientDataKey', {
          alias: names.keyAlias(),
          description: `Data key for client ${client}: tables, inbox bucket, review topic, trigger queue`,
          enableKeyRotation: true,
          removalPolicy,
        })
      : undefined;
    const tableEncryption = dataKey
      ? { encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED, encryptionKey: dataKey }
      : {};

    // Project root: process.cwd() is agentcore/cdk/ → go up 2 levels
    const projectRoot = path.resolve(process.cwd(), '..', '..');

    // ─── DynamoDB Tables ───────────────────────────────────────────

    const policiesTable = new dynamodb.Table(this, 'PoliciesTable', {
      tableName: names.table('Policies'),
      partitionKey: { name: 'policy_number', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
      ...tableEncryption,
    });

    const claimsTable = new dynamodb.Table(this, 'ClaimsTable', {
      tableName: names.table('Claims'),
      partitionKey: { name: 'claim_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
      ...tableEncryption,
    });

    // Add GSI for listing claims by status (avoids full table scan)
    claimsTable.addGlobalSecondaryIndex({
      indexName: 'status-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'created_at', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const reviewsTable = new dynamodb.Table(this, 'ReviewsTable', {
      tableName: names.table('Reviews'),
      partitionKey: { name: 'review_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
      ...tableEncryption,
    });

    // Add GSI for finding reviews by claim_id (avoids full table scan)
    reviewsTable.addGlobalSecondaryIndex({
      indexName: 'claim-id-index',
      partitionKey: { name: 'claim_id', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // ─── SNS Topic (human review alerts) ──────────────────────────

    const reviewTopic = new sns.Topic(this, 'ReviewTopic', {
      topicName: names.topic(),
      masterKey: dataKey,
    });

    // ─── S3 Bucket (claims email inbox) ───────────────────────────

    const inboxBucket = new s3.Bucket(this, 'InboxBucket', {
      bucketName: names.bucket(),
      removalPolicy,
      autoDeleteObjects: destroyOnDelete,
      eventBridgeEnabled: true,
      ...(dataKey ? { encryption: s3.BucketEncryption.KMS, encryptionKey: dataKey, bucketKeyEnabled: true } : {}),
    });

    // ─── Cognito values (externally managed — read from env) ──────
    // Cognito is created by scripts/setup_cognito.sh BEFORE deploy.
    // These values are passed via environment variables at synth time.
    this.cognitoDiscoveryUrl = process.env.COGNITO_DISCOVERY_URL || 'PLACEHOLDER_DISCOVERY_URL';
    this.cognitoClientId = process.env.AGENTCORE_GATEWAY_CLIENT_ID || 'PLACEHOLDER_CLIENT_ID';

    // ─── Lambda Tool Functions ─────────────────────────────────────

    const lambdasPath = path.join(projectRoot, 'lambdas');

    const policyLookupFn = new lambda_.Function(this, 'PolicyLookupFn', {
      functionName: names.fn('PolicyLookup'),
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'policy_lookup')),
      environment: { POLICIES_TABLE: policiesTable.tableName },
      timeout: cdk.Duration.seconds(10),
    });
    policiesTable.grantReadData(policyLookupFn);

    const createClaimFn = new lambda_.Function(this, 'CreateClaimFn', {
      functionName: names.fn('CreateClaim'),
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'create_claim')),
      environment: { CLAIMS_TABLE: claimsTable.tableName },
      timeout: cdk.Duration.seconds(10),
    });
    claimsTable.grantReadWriteData(createClaimFn);

    const humanReviewFn = new lambda_.Function(this, 'HumanReviewFn', {
      functionName: names.fn('HumanReview'),
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'human_review')),
      environment: {
        REVIEWS_TABLE: reviewsTable.tableName,
        REVIEW_SNS_TOPIC_ARN: reviewTopic.topicArn,
      },
      timeout: cdk.Duration.seconds(10),
    });
    reviewsTable.grantReadWriteData(humanReviewFn);
    reviewTopic.grantPublish(humanReviewFn);

    const notificationFn = new lambda_.Function(this, 'NotificationFn', {
      functionName: names.fn('Notification'),
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'notification')),
      environment: { SENDER_EMAIL: process.env.SENDER_EMAIL || 'noreply@example.com' },
      timeout: cdk.Duration.seconds(10),
    });
    notificationFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ses:SendEmail', 'ses:SendRawEmail'],
        resources: [`arn:aws:ses:${stack.region}:${stack.account}:identity/*`],
      })
    );

    const listPendingFn = new lambda_.Function(this, 'ListPendingFn', {
      functionName: names.fn('ListPending'),
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'list_pending_claims')),
      environment: { CLAIMS_TABLE: claimsTable.tableName },
      timeout: cdk.Duration.seconds(10),
    });
    claimsTable.grantReadData(listPendingFn);

    const resolveClaimFn = new lambda_.Function(this, 'ResolveClaimFn', {
      functionName: names.fn('ResolveClaim'),
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'resolve_claim')),
      environment: {
        CLAIMS_TABLE: claimsTable.tableName,
        REVIEWS_TABLE: reviewsTable.tableName,
      },
      timeout: cdk.Duration.seconds(10),
    });
    claimsTable.grantReadWriteData(resolveClaimFn);
    reviewsTable.grantReadWriteData(resolveClaimFn);

    // ─── Lambda ARN Map (gateway target name → function ARN) ──────

    this.lambdaArnMap = {
      'policy-lookup': policyLookupFn.functionArn,
      'create-claim': createClaimFn.functionArn,
      'human-review': humanReviewFn.functionArn,
      'notification': notificationFn.functionArn,
      'list-pending-claims': listPendingFn.functionArn,
      'resolve-claim': resolveClaimFn.functionArn,
    };

    // ─── Dead-Letter Queue (failed claim triggers) ────────────────

    const triggerDlq = new sqs.Queue(this, 'TriggerDLQ', {
      queueName: names.dlq(),
      retentionPeriod: cdk.Duration.days(14),
      ...(dataKey
        ? { encryption: sqs.QueueEncryption.KMS, encryptionMasterKey: dataKey }
        : { encryption: sqs.QueueEncryption.SQS_MANAGED }),
    });

    // Alarm when failed claims land in the DLQ — ensures ops visibility
    new cloudwatch.Alarm(this, 'TriggerDLQAlarm', {
      alarmName: names.alarm(),
      alarmDescription: 'Claims trigger DLQ has messages — failed claim processing needs attention',
      metric: triggerDlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.minutes(1),
      }),
      threshold: 1,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // ─── Trigger Lambda (EventBridge → Runtime) ───────────────────

    this.triggerFn = new lambda_.Function(this, 'TriggerFn', {
      functionName: names.fn('Trigger'),
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'trigger')),
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
      ruleName: names.rule(),
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
    // The wording is data (lib/guardrail-topics.ts): the client's own file if it supplied one, otherwise the original wording with
    // no client (so an existing deployment is unchanged) or neutral wording with a client. The blocked-output sentinel is fixed.
    const originalWording = !client && props.clientDisplayName === undefined;
    const displayName = props.clientDisplayName;
    const staff = originalWording ? 'HESTA' : displayName ? displayName : 'the organisation';
    const teamMember = originalWording
      ? 'a HESTA team member'
      : displayName
        ? `a member of the ${displayName} team`
        : 'a member of our team';
    const wording = props.guardrail ?? (originalWording ? LEGACY_GUARDRAIL : NEUTRAL_GUARDRAIL);
    const vars = { staff, teamMember };
    this.adviceGuardrail = new bedrock.CfnGuardrail(this, 'AdviceGuardrail', {
      name: names.guardrail(),
      description: renderGuardrailText(wording.description, vars),
      blockedInputMessaging: renderGuardrailText(wording.blockedInputMessage, vars),
      blockedOutputsMessaging: '[GUARDRAIL_BLOCKED_ADVICE]',
      topicPolicyConfig: {
        topicsConfig: wording.topics.map((t) => ({
          name: t.name,
          type: 'DENY',
          definition: t.definition,
          examples: t.examples,
        })),
      },
    });

    // ─── Outputs ──────────────────────────────────────────────────

    new cdk.CfnOutput(this, 'InboxBucketName', { value: inboxBucket.bucketName });
    new cdk.CfnOutput(this, 'ReviewTopicArn', { value: reviewTopic.topicArn });
    new cdk.CfnOutput(this, 'TriggerDLQUrl', { value: triggerDlq.queueUrl });
    new cdk.CfnOutput(this, 'AdviceGuardrailId', { value: this.adviceGuardrail.attrGuardrailId });
    if (client) {
      new cdk.CfnOutput(this, 'ClientSlug', { value: client, description: 'The client this stack belongs to' });
    }
  }
}
