/**
 * Tests for guardrail-topics.ts: the guardrail's wording as a client-supplied file.
 * Run with `npm test`. Uses Node's built-in test runner, so no extra dependency.
 */
import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  LEGACY_GUARDRAIL,
  NEUTRAL_GUARDRAIL,
  loadGuardrailConfig,
  parseGuardrailConfig,
  renderGuardrailText,
  resolveGuardrailConfig,
} from '../lib/guardrail-topics';

const VALID = {
  version: 1,
  description: 'Denies personal advice; {staff} staff handle enquiries.',
  blockedInputMessage: 'This enquiry needs {teamMember}. We cannot give personal advice.',
  topics: [
    {
      name: 'PolicyAdvice',
      definition: 'Advice on which cover to buy, keep or cancel, tailored to the person asking.',
      examples: ['Should I cancel my policy?', 'Which level of cover is best for me?'],
    },
  ],
};

function tempDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'guardrail-test-'));
}

function writeJson(dir: string, name: string, value: unknown): string {
  const file = path.join(dir, name);
  fs.writeFileSync(file, typeof value === 'string' ? value : JSON.stringify(value), 'utf8');
  return file;
}

const asFile = (g: typeof LEGACY_GUARDRAIL) => ({ version: 1, ...g });

// ---- the built-in defaults --------------------------------------------------------------------------------------------

test('both built-in defaults pass the same validation a client file does', () => {
  // The limits are Bedrock's. A default that broke one would fail at deploy time, not here, so check it here.
  assert.doesNotThrow(() => parseGuardrailConfig(asFile(LEGACY_GUARDRAIL), 'legacy'));
  assert.doesNotThrow(() => parseGuardrailConfig(asFile(NEUTRAL_GUARDRAIL), 'neutral'));
});

test('the neutral default names no organisation and no industry', () => {
  // The placeholder names ({teamMember}) are code, not wording a reader sees, so they are removed before the check.
  const text = JSON.stringify(NEUTRAL_GUARDRAIL).replace(/\{[^{}]*\}/g, '');
  assert.doesNotMatch(text, /hesta|super|fund|member|insur|policy/i);
});

test('the original wording is kept character for character', () => {
  // An existing deployment must synthesise exactly this, or CloudFormation updates a guardrail that did not change.
  assert.equal(LEGACY_GUARDRAIL.blockedInputMessage, 'This enquiry needs {teamMember} — we can’t provide personal financial advice.');
  assert.equal(LEGACY_GUARDRAIL.topics[0].name, 'PersonalFinancialAdvice');
  assert.equal(LEGACY_GUARDRAIL.topics[0].examples.length, 5);
});

// ---- a valid file ---------------------------------------------------------------------------------------------------

test('a valid file loads and keeps its wording', () => {
  const file = writeJson(tempDir(), 'topics.json', VALID);
  const config = loadGuardrailConfig(file);
  assert.equal(config.topics[0].name, 'PolicyAdvice');
  assert.deepEqual(config.topics[0].examples, VALID.topics[0].examples);
  assert.equal(config.blockedInputMessage, VALID.blockedInputMessage);
});

test('placeholders are filled from the client name', () => {
  const text = renderGuardrailText('{teamMember} at {staff}; {staff} again', { staff: 'Acme Insurance', teamMember: 'a member of the Acme Insurance team' });
  assert.equal(text, 'a member of the Acme Insurance team at Acme Insurance; Acme Insurance again');
});

// ---- a bad file says everything that is wrong, once ------------------------------------------------------------------

test('every problem is reported together, with the file named', () => {
  const bad = {
    version: 2,
    colour: 'red',
    description: 'Uses {nonsense}',
    blockedInputMessage: '',
    topics: [{ name: 'Good Name', definition: 'x'.repeat(201), examples: ['a', 'b', 'c', 'd', 'e', 'f'], extra: true }],
  };
  assert.throws(() => parseGuardrailConfig(bad, 'client/topics.json'), (err: Error) => {
    for (const expected of [
      'client/topics.json',
      '"version" must be 1',
      'unknown key "colour"',
      '{nonsense}',
      '"blockedInputMessage" must be non-empty text',
      'topics[0].definition is 201 characters',
      'topics[0].examples has 6 entries',
      'unknown key "extra"',
    ]) {
      assert.ok(err.message.includes(expected), `missing: ${expected}\n${err.message}`);
    }
    return true;
  });
});

test('placeholders are refused inside a topic, where they would reach Bedrock as literal braces', () => {
  const bad = { ...VALID, topics: [{ ...VALID.topics[0], definition: 'Advice for {staff} customers.' }] };
  assert.throws(() => parseGuardrailConfig(bad, 'f'), /topics\[0\]\.definition may not contain \{staff\}/);
});

test('two topics may not share a name, and a name may not use characters Bedrock rejects', () => {
  const twice = { ...VALID, topics: [VALID.topics[0], VALID.topics[0]] };
  assert.throws(() => parseGuardrailConfig(twice, 'f'), /is used twice/);
  const odd = { ...VALID, topics: [{ ...VALID.topics[0], name: 'Bad/Name' }] };
  assert.throws(() => parseGuardrailConfig(odd, 'f'), /may use only letters/);
});

test('a file that is not JSON, or not an object, is refused in plain words', () => {
  const dir = tempDir();
  assert.throws(() => loadGuardrailConfig(writeJson(dir, 'a.json', '{ not json')), /is not valid JSON/);
  assert.throws(() => parseGuardrailConfig([], 'f'), /must hold one JSON object/);
  assert.throws(() => loadGuardrailConfig(path.join(dir, 'missing.json')), /cannot be read/);
});

// ---- finding the file --------------------------------------------------------------------------------------------------

test('with no file anywhere, nothing is resolved and the built-in default applies', () => {
  assert.deepEqual(resolveGuardrailConfig(undefined, tempDir()), {});
  assert.deepEqual(resolveGuardrailConfig('   ', tempDir()), {});
});

test('agentcore/guardrail.json is found by convention', () => {
  const dir = tempDir();
  const file = writeJson(dir, 'guardrail.json', VALID);
  const resolved = resolveGuardrailConfig(undefined, dir);
  assert.equal(resolved.source, file);
  assert.equal(resolved.config?.topics[0].name, 'PolicyAdvice');
});

test('a named file beats the conventional one, and a relative path is read from the config root', () => {
  const dir = tempDir();
  writeJson(dir, 'guardrail.json', VALID);
  writeJson(dir, 'other.json', { ...VALID, topics: [{ ...VALID.topics[0], name: 'OtherTopic' }] });
  assert.equal(resolveGuardrailConfig('other.json', dir).config?.topics[0].name, 'OtherTopic');
});

test('a named file that does not exist stops the build, it does not fall back to the default wording', () => {
  // A typo in the path must never quietly deploy someone else's wording.
  const dir = tempDir();
  writeJson(dir, 'guardrail.json', VALID);
  assert.throws(() => resolveGuardrailConfig('typo.json', dir), /cannot be read/);
});
