/**
 * Tests for naming.ts: the rules that let several clients share one AWS account (ADR-0015, Q7).
 * Run with `npm test`. Uses Node's built-in test runner, so no extra dependency.
 */
import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import { FUNCTION_KINDS, ResourceNames, TABLE_KINDS, parseClient } from '../lib/naming';

const ACCOUNT = '111122223333';
const REGION = 'ap-southeast-2';

// The original, shared names. An existing deployment must keep producing exactly these, or CloudFormation will try to
// replace a table or a bucket. This list is the backward-compatibility contract.
const ORIGINAL_NAMES = [
  'ClaimsAgent-dev-Policies',
  'ClaimsAgent-dev-Claims',
  'ClaimsAgent-dev-Reviews',
  'ClaimsAgent-PolicyLookup',
  'ClaimsAgent-CreateClaim',
  'ClaimsAgent-HumanReview',
  'ClaimsAgent-Notification',
  'ClaimsAgent-ListPending',
  'ClaimsAgent-ResolveClaim',
  'ClaimsAgent-Trigger',
  'ClaimsAgent-HumanReview', // the SNS topic shares the human-review function's name in the original
  'ClaimsAgent-TriggerDLQ',
  'ClaimsAgent-FailedClaims',
  'ClaimsAgent-InboxTrigger',
  'ClaimsAgent-dev-NoPersonalAdvice',
  `claims-inbox-${ACCOUNT}-${REGION}`,
];

test('with no client, every name is exactly the original', () => {
  const names = new ResourceNames(undefined, 'dev', ACCOUNT, REGION);
  assert.deepEqual(names.all(), ORIGINAL_NAMES);
});

test('with a client, every name carries the client slug', () => {
  const names = new ResourceNames('acme', 'dev', ACCOUNT, REGION);
  for (const name of names.all()) {
    assert.ok(name.startsWith('acme-'), `${name} does not start with the client slug`);
  }
  assert.equal(names.table('Policies'), 'acme-dev-Policies');
  assert.equal(names.bucket(), `acme-inbox-${ACCOUNT}-${REGION}`);
  assert.equal(names.keyAlias(), 'alias/acme-dev-data');
});

test('two clients in one account and region share no name, and neither shares one with the original', () => {
  const sets = [undefined, 'acme', 'globex', 'fund-a1'].map(c => new Set(new ResourceNames(c, 'dev', ACCOUNT, REGION).all()));
  for (let i = 0; i < sets.length; i++) {
    for (let j = i + 1; j < sets.length; j++) {
      const shared = [...sets[i]].filter(n => sets[j].has(n));
      assert.deepEqual(shared, [], `sets ${i} and ${j} share ${shared.join(', ')}`);
    }
  }
});

test('the stage is part of table and guardrail names, so one client can have dev and prod', () => {
  const dev = new ResourceNames('acme', 'dev', ACCOUNT, REGION);
  const prod = new ResourceNames('acme', 'prod', ACCOUNT, REGION);
  assert.notEqual(dev.table('Claims'), prod.table('Claims'));
  assert.notEqual(dev.guardrail(), prod.guardrail());
});

test('the number of names matches the number of resource kinds', () => {
  const all = new ResourceNames('acme', 'dev', ACCOUNT, REGION).all();
  assert.equal(all.length, TABLE_KINDS.length + FUNCTION_KINDS.length + 6);
});

test('valid client slugs are accepted, and empty means no client', () => {
  for (const ok of ['acme', 'hesta', 'fund-a1', 'abc', 'a'.repeat(24)]) {
    assert.equal(parseClient(ok), ok);
  }
  for (const none of [undefined, '', '   ']) {
    assert.equal(parseClient(none), undefined);
  }
  assert.equal(parseClient('  acme  '), 'acme');
});

test('malformed or reserved client slugs are refused with a readable message', () => {
  for (const bad of ['a', 'ab', 'Acme', 'acme_corp', '-acme', 'acme-', 'ac--me', '1acme', 'a'.repeat(25), 'ac me', 'claimsagent', 'aws']) {
    assert.throws(() => parseClient(bad), /Invalid client/, `"${bad}" should be refused`);
  }
});

test('the longest allowed slug still fits every AWS length limit', () => {
  const names = new ResourceNames('a'.repeat(24), 'prod', ACCOUNT, 'ap-southeast-2');
  assert.ok(names.bucket().length <= 63);
  assert.ok(names.guardrail().length <= 50);
});

test('a name that would break an AWS limit fails up front and says which', () => {
  assert.throws(() => new ResourceNames('a'.repeat(24), 'a-very-long-stage-name-indeed', ACCOUNT, REGION), /too long for AWS.*guardrail/);
});
