/**
 * Physical resource names for a client stack.
 *
 * Why this exists: the supplementary infrastructure used fixed names (`ClaimsAgent-PolicyLookup`, a bucket called
 * `claims-inbox-<account>-<region>`, and so on). Two stacks in one account and region would collide. ADR-0015 (Q7) puts
 * several clients in one account, one stack each, so every name must carry the client.
 *
 * Two modes, and the difference is the point:
 *  - no client (the default): the ORIGINAL names, exactly. An existing deployment synthesises the same names, so CloudFormation
 *    does not try to replace a table or a bucket.
 *  - a client: every name is prefixed with the client slug, and a test proves two clients share no name.
 *
 * Names the AgentCore construct derives from the project name (gateway, memory, evaluator, runtime, repository) are NOT here:
 * they need a per-client project name in agentcore.json, which `scripts/assemble_client.py` writes.
 */

export const LEGACY_PREFIX = 'ClaimsAgent';

/** 3 to 24 characters, lower case, letters, digits and single hyphens, starting with a letter and not ending in a hyphen. */
const SLUG = /^[a-z][a-z0-9-]{1,22}[a-z0-9]$/;
/** Slugs that would be confused with the original shared names or with AWS itself. */
const RESERVED = new Set(['claimsagent', 'agentcore', 'aws', 'amazon']);

/** Validate a client slug. Empty means "no client" (the original shared names). Throws a readable error otherwise. */
export function parseClient(raw: string | undefined): string | undefined {
  const value = (raw ?? '').trim();
  if (value === '') {
    return undefined;
  }
  if (!SLUG.test(value) || value.includes('--') || RESERVED.has(value)) {
    throw new Error(
      `Invalid client "${value}". Use 3 to 24 lower-case letters, digits and single hyphens, starting with a letter and not ending ` +
        `in a hyphen (not one of: ${[...RESERVED].join(', ')}).`
    );
  }
  return value;
}

export type TableKind = 'Policies' | 'Claims' | 'Reviews';
export type FunctionKind =
  | 'PolicyLookup'
  | 'CreateClaim'
  | 'HumanReview'
  | 'Notification'
  | 'ListPending'
  | 'ResolveClaim'
  | 'Trigger';

export const TABLE_KINDS: TableKind[] = ['Policies', 'Claims', 'Reviews'];
export const FUNCTION_KINDS: FunctionKind[] = [
  'PolicyLookup',
  'CreateClaim',
  'HumanReview',
  'Notification',
  'ListPending',
  'ResolveClaim',
  'Trigger',
];

// The AWS limits that a long slug could break. Checked up front so the failure names the cause, not a CloudFormation error.
const MAX = { bucket: 63, lambda: 64, guardrail: 50, queue: 80, rule: 64 };

export class ResourceNames {
  constructor(
    readonly client: string | undefined,
    readonly stage: string,
    readonly account: string,
    readonly region: string
  ) {
    this.checkLengths();
  }

  private get prefix(): string {
    return this.client ?? LEGACY_PREFIX;
  }

  table(kind: TableKind): string {
    return `${this.prefix}-${this.stage}-${kind}`;
  }
  fn(kind: FunctionKind): string {
    return `${this.prefix}-${kind}`;
  }
  topic(): string {
    return `${this.prefix}-HumanReview`;
  }
  dlq(): string {
    return `${this.prefix}-TriggerDLQ`;
  }
  alarm(): string {
    return `${this.prefix}-FailedClaims`;
  }
  rule(): string {
    return `${this.prefix}-InboxTrigger`;
  }
  guardrail(): string {
    return `${this.prefix}-${this.stage}-NoPersonalAdvice`;
  }
  bucket(): string {
    return this.client
      ? `${this.client}-inbox-${this.account}-${this.region}`
      : `claims-inbox-${this.account}-${this.region}`;
  }
  keyAlias(): string {
    return `alias/${this.prefix}-${this.stage}-data`;
  }

  /** Every physical name this class produces, for tests and for the disjointness check. */
  all(): string[] {
    return [
      ...TABLE_KINDS.map(k => this.table(k)),
      ...FUNCTION_KINDS.map(k => this.fn(k)),
      this.topic(),
      this.dlq(),
      this.alarm(),
      this.rule(),
      this.guardrail(),
      this.bucket(),
    ];
  }

  private checkLengths(): void {
    const over: string[] = [];
    const check = (label: string, name: string, limit: number) => {
      if (name.length > limit) {
        over.push(`${label} "${name}" is ${name.length} characters (limit ${limit})`);
      }
    };
    check('bucket', this.bucket(), MAX.bucket);
    check('guardrail', this.guardrail(), MAX.guardrail);
    check('queue', this.dlq(), MAX.queue);
    check('rule', this.rule(), MAX.rule);
    FUNCTION_KINDS.forEach(k => check('function', this.fn(k), MAX.lambda));
    if (over.length > 0) {
      throw new Error(`A resource name is too long for AWS: ${over.join('; ')}. Use a shorter client slug.`);
    }
  }
}
