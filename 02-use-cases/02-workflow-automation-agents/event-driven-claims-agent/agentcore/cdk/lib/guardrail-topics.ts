/**
 * The guardrail's wording, as data rather than code.
 *
 * Why this exists: the Bedrock Guardrail denies personal advice, and what counts as "personal advice" is the client's language
 * (a super fund's "which option is best for me?", an insurer's "should I cancel my policy?"). That wording used to sit inside
 * infra-construct.ts, so a new client meant editing infrastructure code. Now a client supplies a JSON file and the code stays
 * neutral. The file is plain JSON so anything can write it, including an agent working from a client's compliance material.
 *
 * Where the wording comes from, in order:
 *  1. a file named by `-c guardrailTopics=<path>`, AGENTCORE_GUARDRAIL_TOPICS, or `guardrailTopics` in cdk.json
 *  2. `agentcore/guardrail.json`, if present (scripts/assemble_client.py puts the client's file there)
 *  3. the built-in default: the ORIGINAL wording with no client, so an existing deployment is unchanged, and neutral wording
 *     with a client.
 *
 * The blocked-output sentinel is deliberately not configurable. The Reviewer agent and hesta/settings.py match it by value.
 */
import * as fs from 'fs';
import * as path from 'path';

export interface GuardrailTopic {
  name: string;
  definition: string;
  examples: string[];
}

export interface GuardrailConfig {
  /** Free text for the console. May use {staff}. */
  description: string;
  /** What the member sees when their message is blocked. May use {teamMember} and {staff}. */
  blockedInputMessage: string;
  topics: GuardrailTopic[];
}

/** The words the construct fills in from the client's display name. Anything else in braces is rejected. */
export const PLACEHOLDERS = ['staff', 'teamMember'] as const;
export type Placeholder = (typeof PLACEHOLDERS)[number];

// Bedrock's own limits on a denied topic. Checking here turns a CloudFormation failure at deploy time into a readable error at synth.
const LIMITS = { topics: 30, name: 100, definition: 200, examples: 5, example: 100, message: 500, description: 200 };
const TOPIC_NAME = /^[0-9a-zA-Z_ !?.-]+$/;
const TOP_LEVEL_KEYS = new Set(['version', 'description', 'blockedInputMessage', 'topics']);
const TOPIC_KEYS = new Set(['name', 'definition', 'examples']);

/** The original deployment's wording, character for character. Used only when there is no client and no file. */
export const LEGACY_GUARDRAIL: GuardrailConfig = {
  description: 'Denies personal financial/investment/product advice; {staff} staff handle advice enquiries.',
  blockedInputMessage: 'This enquiry needs {teamMember} — we can’t provide personal financial advice.',
  topics: [
    {
      name: 'PersonalFinancialAdvice',
      definition:
        'Personal financial or investment advice, or product/option recommendations, tailored to an ' +
        "individual member's own circumstances (what they personally should do with their super).",
      examples: [
        'Which super option is best for me?',
        'Should I switch to the high growth option?',
        'What should I invest my super in?',
        'Is it a good idea for me to roll over my other fund for my situation?',
        'Can you recommend the best investment choice for me?',
      ],
    },
  ],
};

/** What a client gets until it supplies its own file. Names no industry and no organisation. */
export const NEUTRAL_GUARDRAIL: GuardrailConfig = {
  description: 'Denies personal advice tailored to an individual’s circumstances; staff handle advice enquiries.',
  blockedInputMessage: 'This enquiry needs {teamMember} — we can’t provide personal financial advice.',
  topics: [
    {
      name: 'PersonalFinancialAdvice',
      definition:
        'Personal financial or investment advice, or product recommendations, tailored to an ' +
        "individual's own circumstances (what they personally should do or choose).",
      examples: [
        'Which option is best for me?',
        'Should I switch to a different product?',
        'What should I choose for my situation?',
        'Is it a good idea for me to change products?',
        'Can you recommend the best choice for me?',
      ],
    },
  ],
};

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function placeholdersIn(text: string): string[] {
  return [...text.matchAll(/\{([^{}]*)\}/g)].map((m) => m[1]);
}

/**
 * Check a parsed file and return it as a GuardrailConfig. Collects every problem and throws once, so a person fixing the file
 * sees all of them together. `source` is only used in the message.
 */
export function parseGuardrailConfig(raw: unknown, source: string): GuardrailConfig {
  const problems: string[] = [];
  const bad = (msg: string) => problems.push(msg);

  const checkText = (where: string, value: unknown, max: number, opts: { allowPlaceholders: boolean }): value is string => {
    if (typeof value !== 'string' || value.trim() === '') {
      bad(`${where} must be non-empty text`);
      return false;
    }
    if (value.length > max) {
      bad(`${where} is ${value.length} characters; the most Bedrock allows is ${max}`);
    }
    for (const token of placeholdersIn(value)) {
      if (!opts.allowPlaceholders) {
        bad(`${where} may not contain {${token}}; placeholders are only for description and blockedInputMessage`);
      } else if (!(PLACEHOLDERS as readonly string[]).includes(token)) {
        bad(`${where} uses {${token}}; the placeholders are ${PLACEHOLDERS.map((p) => `{${p}}`).join(' and ')}`);
      }
    }
    return true;
  };

  if (!isObject(raw)) {
    throw new Error(`Guardrail topics file ${source} is not valid:\n - the file must hold one JSON object`);
  }
  for (const key of Object.keys(raw)) {
    if (!TOP_LEVEL_KEYS.has(key)) bad(`unknown key "${key}" (allowed: ${[...TOP_LEVEL_KEYS].join(', ')})`);
  }
  if (raw.version !== 1) bad('"version" must be 1');

  checkText('"description"', raw.description, LIMITS.description, { allowPlaceholders: true });
  checkText('"blockedInputMessage"', raw.blockedInputMessage, LIMITS.message, { allowPlaceholders: true });

  const topics: GuardrailTopic[] = [];
  if (!Array.isArray(raw.topics) || raw.topics.length === 0) {
    bad('"topics" must be a list with at least one topic');
  } else {
    if (raw.topics.length > LIMITS.topics) bad(`"topics" has ${raw.topics.length} entries; the most Bedrock allows is ${LIMITS.topics}`);
    const seen = new Set<string>();
    raw.topics.forEach((t, i) => {
      const where = `topics[${i}]`;
      if (!isObject(t)) {
        bad(`${where} must be an object with name, definition and examples`);
        return;
      }
      for (const key of Object.keys(t)) {
        if (!TOPIC_KEYS.has(key)) bad(`${where} has unknown key "${key}" (allowed: ${[...TOPIC_KEYS].join(', ')})`);
      }
      if (checkText(`${where}.name`, t.name, LIMITS.name, { allowPlaceholders: false })) {
        if (!TOPIC_NAME.test(t.name)) bad(`${where}.name "${t.name}" may use only letters, digits, spaces, and _ - ! ? .`);
        if (seen.has(t.name)) bad(`${where}.name "${t.name}" is used twice`);
        seen.add(t.name);
      }
      checkText(`${where}.definition`, t.definition, LIMITS.definition, { allowPlaceholders: false });
      let examples: string[] = [];
      if (!Array.isArray(t.examples) || t.examples.length === 0) {
        bad(`${where}.examples must be a list with at least one example`);
      } else {
        if (t.examples.length > LIMITS.examples) bad(`${where}.examples has ${t.examples.length} entries; the most Bedrock allows is ${LIMITS.examples}`);
        examples = t.examples.filter((e, j) => checkText(`${where}.examples[${j}]`, e, LIMITS.example, { allowPlaceholders: false })) as string[];
      }
      if (typeof t.name === 'string' && typeof t.definition === 'string') {
        topics.push({ name: t.name, definition: t.definition, examples });
      }
    });
  }

  if (problems.length > 0) {
    throw new Error(`Guardrail topics file ${source} is not valid:\n${problems.map((p) => ` - ${p}`).join('\n')}`);
  }
  return {
    description: raw.description as string,
    blockedInputMessage: raw.blockedInputMessage as string,
    topics,
  };
}

/** Read and check one file. */
export function loadGuardrailConfig(file: string): GuardrailConfig {
  let text: string;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    throw new Error(`Guardrail topics file ${file} cannot be read. Check the path.`);
  }
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    throw new Error(`Guardrail topics file ${file} is not valid JSON: ${(e as Error).message}`);
  }
  return parseGuardrailConfig(raw, file);
}

export interface ResolvedGuardrail {
  config?: GuardrailConfig;
  /** The file it came from, for the stack output. Unset when the built-in default applies. */
  source?: string;
}

/**
 * Find the client's file, if there is one. `explicit` is a path the person named (it must exist, or this throws: a typo must not
 * quietly fall back to the default). Without it, `<configRoot>/guardrail.json` is used when present. Relative paths resolve
 * against `configRoot`, the project's agentcore/ directory.
 */
export function resolveGuardrailConfig(explicit: string | undefined, configRoot: string): ResolvedGuardrail {
  const named = (explicit ?? '').trim();
  if (named !== '') {
    const file = path.resolve(configRoot, named);
    return { config: loadGuardrailConfig(file), source: file };
  }
  const conventional = path.join(configRoot, 'guardrail.json');
  if (fs.existsSync(conventional)) {
    return { config: loadGuardrailConfig(conventional), source: conventional };
  }
  return {};
}

/** Fill {staff} and {teamMember}. parseGuardrailConfig has already rejected any other placeholder. */
export function renderGuardrailText(template: string, vars: Record<Placeholder, string>): string {
  return template.replace(/\{(staff|teamMember)\}/g, (_, key: Placeholder) => vars[key]);
}
