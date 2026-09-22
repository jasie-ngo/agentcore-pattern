/**
 * InfraConstruct: Supplementary infrastructure for the Claims Agent.
 *
 * Creates resources that the AgentCore CLI cannot manage natively:
 * - DynamoDB tables (Members, Cases, HumanReview)
 * - S3 bucket (claims email inbox, EventBridge enabled)
 * - Lambda tool functions wired to Gateway via lambdaArnMap
 * - Trigger Lambda (EventBridge → Runtime invocation)
 * - EventBridge rule (S3 PutObject → Trigger)
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
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as events from 'aws-cdk-lib/aws-events';
import * as eventsTargets from 'aws-cdk-lib/aws-events-targets';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import { Construct } from 'constructs';
import * as path from 'path';

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
    const resourcePrefix = stack.stackName;
    cdk.Tags.of(this).add('id', '2520121');
    const destroyOnDelete = props.destroyOnDelete ?? true;
    const removalPolicy = destroyOnDelete ? cdk.RemovalPolicy.DESTROY : cdk.RemovalPolicy.RETAIN;

    // Project root: process.cwd() is agentcore/cdk/ → go up 2 levels
    const projectRoot = path.resolve(process.cwd(), '..', '..');

    // ─── DynamoDB Tables ───────────────────────────────────────────

    const membersTable = new dynamodb.Table(this, 'MembersTable', {
      tableName: `${resourcePrefix}-Hesta-members`,
      partitionKey: { name: 'member_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });
    membersTable.addGlobalSecondaryIndex({
      indexName: 'email-index',
      partitionKey: { name: 'email', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const casesTable = new dynamodb.Table(this, 'CasesTable', {
      tableName: `${resourcePrefix}-Hesta-cases`,
      partitionKey: { name: 'case_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });
    casesTable.addGlobalSecondaryIndex({
      indexName: 'member_id-index',
      partitionKey: { name: 'member_id', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const humanReviewTable = new dynamodb.Table(this, 'HumanReviewTable', {
      tableName: `${resourcePrefix}-Hesta-humanreview`,
      partitionKey: { name: 'review_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });

    // ─── S3 Bucket (claims email inbox) ───────────────────────────

    const inboxBucket = new s3.Bucket(this, 'InboxBucket', {
      bucketName: 'hesta-poc-agentcore-s3',
      removalPolicy,
      autoDeleteObjects: destroyOnDelete,
      eventBridgeEnabled: true,
    });

    // ─── Cognito values (externally managed — read from env) ──────
    // Cognito is created by scripts/setup_cognito.sh BEFORE deploy.
    // These values are passed via environment variables at synth time.
    this.cognitoDiscoveryUrl = process.env.COGNITO_DISCOVERY_URL || 'PLACEHOLDER_DISCOVERY_URL';
    this.cognitoClientId = process.env.AGENTCORE_GATEWAY_CLIENT_ID || 'PLACEHOLDER_CLIENT_ID';

    // ─── Lambda Tool Functions ─────────────────────────────────────

    const lambdasPath = path.join(projectRoot, 'lambdas');

    const memberLookupFn = new lambda_.Function(this, 'MemberLookupFn', {
      functionName: `${resourcePrefix}-MemberLookup`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'member_lookup')),
      environment: { HESTA_MEMBERS_TABLE: membersTable.tableName },
      timeout: cdk.Duration.seconds(10),
    });
    membersTable.grantReadData(memberLookupFn);

    const caseLookupCreationFn = new lambda_.Function(this, 'CaseLookupCreationFn', {
      functionName: `${resourcePrefix}-CaseLookupCreation`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'case_lookup_creation')),
      environment: { HESTA_CASES_TABLE: casesTable.tableName },
      timeout: cdk.Duration.seconds(10),
    });
    casesTable.grantReadWriteData(caseLookupCreationFn);

    const emailReviewFn = new lambda_.Function(this, 'EmailReviewFn', {
      functionName: `${resourcePrefix}-EmailReview`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'email_review')),
      environment: { HESTA_HUMANREVIEW_TABLE: humanReviewTable.tableName },
      timeout: cdk.Duration.seconds(10),
    });
    humanReviewTable.grantReadWriteData(emailReviewFn);

    // ─── Lambda ARN Map (gateway target name → function ARN) ──────

    this.lambdaArnMap = {
      'member-lookup': memberLookupFn.functionArn,
      'case-lookup-creation': caseLookupCreationFn.functionArn,
      'email-review': emailReviewFn.functionArn,
    };

    // ─── Dead-Letter Queue (failed claim triggers) ────────────────

    const triggerDlq = new sqs.Queue(this, 'TriggerDLQ', {
      queueName: `${resourcePrefix}-TriggerDLQ`,
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Alarm when failed claims land in the DLQ — ensures ops visibility
    new cloudwatch.Alarm(this, 'TriggerDLQAlarm', {
      alarmName: `${resourcePrefix}-FailedClaims`,
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
      functionName: `${resourcePrefix}-Trigger`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'trigger')),
      environment: {
        // AGENTCORE_RUNTIME_ARN injected by parent stack after Runtime is created
        AGENTCORE_RUNTIME_ARN: 'PENDING',
        // TODO 2: bounded attachment parsing — fail closed (skipped_reason) rather than
        // silently drop or truncate an oversized/excess attachment.
        MAX_ATTACHMENT_BYTES: '262144',
        MAX_ATTACHMENTS: '5',
      },
      timeout: cdk.Duration.seconds(90),
      deadLetterQueue: triggerDlq,
      retryAttempts: 2,
    });
    inboxBucket.grantRead(this.triggerFn);

    // ─── EventBridge Rule: S3 PutObject → Trigger Lambda ──────────

    new events.Rule(this, 'ClaimInboxRule', {
      ruleName: `${resourcePrefix}-InboxTrigger`,
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

    // ─── Bedrock Guardrail: deny personal financial advice + sensitive account disclosure ────────
    // Platform control (showcase): a denied-topic guardrail attached to the Writer model
    // so the agent cannot generate personal financial/product advice, or state sensitive
    // account-specific details. This is the model-level BACKSTOP only — the authorization
    // decision itself is application logic (MemberProfile.disclosure_state in main.py), and
    // the deterministic pattern scan (agents/disclosure_check.py) is the application-layer
    // check. Works together with the app-layer detection (AI-001) + routing + Reviewer checks.
    this.adviceGuardrail = new bedrock.CfnGuardrail(this, 'AdviceGuardrail', {
      name: `${resourcePrefix}-NoPersonalAdvice`,
      description:
        'Denies personal financial/investment/product advice and sensitive account-detail disclosure; '
        + 'HESTA staff handle advice enquiries and verified account actions.',
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
          {
            name: 'SensitiveAccountDisclosure',
            type: 'DENY',
            // Bedrock caps topic definitions at 200 chars (this is 188) — keep it terse.
            definition:
              'Stating a member\'s specific account details in a reply: balances, transaction or '
              + 'contribution history, payment amounts, tax file numbers, bank/BSB details, or internal '
              + 'account identifiers.',
            examples: [
              'Your current balance is $48,213.55.',
              'We can confirm your BSB is 063-000 and account number 12345678.',
              'Your TFN on file is 123 456 789.',
              'Your last contribution of $2,500 was received on 3 March.',
              'Your bank details for the refund are BSB 063-000, account 87654321.',
            ],
          },
        ],
      },
    });

    // ─── Outputs ──────────────────────────────────────────────────

    new cdk.CfnOutput(this, 'InboxBucketName', { value: inboxBucket.bucketName });
    new cdk.CfnOutput(this, 'TriggerDLQUrl', { value: triggerDlq.queueUrl });
    new cdk.CfnOutput(this, 'AdviceGuardrailId', { value: this.adviceGuardrail.attrGuardrailId });
  }
}
