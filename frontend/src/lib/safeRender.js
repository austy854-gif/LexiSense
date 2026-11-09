/**
 * Defensive rendering helpers for AI-produced analysis fields (LX-ENG-KT-01).
 *
 * The analysis JSON comes from an LLM, so its shape can drift: a key term that
 * should be a string can arrive as `{label, value}`, `{term, definition}`, a
 * number, null, or a nested object. React throws when it is asked to render a
 * plain object as a child, which took the whole app down to ErrorFallback.
 * These helpers turn any value into plain text. They never produce HTML, so
 * callers render the result as a normal text child (no dangerouslySetInnerHTML).
 */

const MAX_DEPTH = 3;
const MAX_TEXT_LENGTH = 2000;

// Keys an LLM commonly uses for the "name" and the "body" of a pair.
const LABEL_KEYS = ['label', 'term', 'name', 'title', 'key', 'clause', 'heading'];
const VALUE_KEYS = ['value', 'definition', 'description', 'detail', 'details', 'text', 'content', 'summary', 'explanation'];

function clip(text) {
  return text.length > MAX_TEXT_LENGTH ? `${text.slice(0, MAX_TEXT_LENGTH - 1)}…` : text;
}

function firstPresent(obj, keys) {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      const v = obj[key];
      if (v !== null && v !== undefined && v !== '') return { key, value: v };
    }
  }
  return null;
}

function textOf(value, depth) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'bigint') return String(value);
  if (typeof value !== 'object') return ''; // functions, symbols
  if (depth >= MAX_DEPTH) return '';
  if (Array.isArray(value)) {
    return value.map((v) => textOf(v, depth + 1)).filter(Boolean).join(', ');
  }
  const pair = labelValueOf(value, depth);
  if (pair.label && pair.text) return `${pair.label}: ${pair.text}`;
  return pair.label || pair.text;
}

function labelValueOf(obj, depth) {
  const label = firstPresent(obj, LABEL_KEYS);
  const body = firstPresent(obj, VALUE_KEYS.filter((k) => !label || k !== label.key));
  if (label || body) {
    return {
      label: label ? textOf(label.value, depth + 1) : '',
      text: body ? textOf(body.value, depth + 1) : '',
    };
  }
  // Unknown object shape: show its primitive-ish entries as "key: value".
  const parts = Object.keys(obj)
    .map((k) => {
      const t = textOf(obj[k], depth + 1);
      return t ? `${k}: ${t}` : '';
    })
    .filter(Boolean);
  return { label: '', text: parts.join('; ') };
}

/** Any value → a plain display string ('' when there is nothing to show). */
export function toDisplayText(value) {
  try {
    return clip(textOf(value, 0));
  } catch {
    return '';
  }
}

/**
 * Any value → an array, so `.map` is always safe. A single non-array value
 * becomes a one-item list; null/undefined become an empty list.
 */
export function toList(value) {
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined || value === '') return [];
  return [value];
}

/**
 * Normalise one key term to `{ label, text }` for a "label: value" render.
 * `label` is '' for plain-string terms. Returns null when nothing is shown.
 */
export function normalizeKeyTerm(term) {
  try {
    if (term && typeof term === 'object' && !Array.isArray(term)) {
      const { label, text } = labelValueOf(term, 0);
      if (!label && !text) return null;
      if (label && text) return { label: clip(label), text: clip(text) };
      return { label: '', text: clip(label || text) };
    }
    const text = toDisplayText(term);
    return text ? { label: '', text } : null;
  } catch {
    return null;
  }
}

/** Key terms list → normalised, non-empty entries. */
export function normalizeKeyTerms(value) {
  return toList(value).map(normalizeKeyTerm).filter(Boolean);
}

/** Plain-text list (parties, recommendations) → non-empty strings. */
export function normalizeTextList(value) {
  return toList(value).map(toDisplayText).filter(Boolean);
}

const SEVERITIES = new Set(['high', 'medium', 'low']);

/** Normalise one risk to `{ risk, severity, recommendation }` strings. */
export function normalizeRisk(risk) {
  try {
    if (risk && typeof risk === 'object' && !Array.isArray(risk)) {
      const title = toDisplayText(
        risk.risk ?? risk.title ?? risk.name ?? risk.issue ?? risk.description ?? risk.label,
      );
      const rawSeverity = toDisplayText(risk.severity ?? risk.level ?? risk.riskLevel).toLowerCase();
      const recommendation = toDisplayText(risk.recommendation ?? risk.mitigation);
      const fallback = title || recommendation ? '' : toDisplayText(risk);
      const text = title || fallback;
      if (!text && !recommendation) return null;
      return {
        risk: text,
        severity: SEVERITIES.has(rawSeverity) ? rawSeverity : rawSeverity || null,
        recommendation,
      };
    }
    const text = toDisplayText(risk);
    return text ? { risk: text, severity: null, recommendation: '' } : null;
  } catch {
    return null;
  }
}

/** Risks list → normalised, non-empty entries. */
export function normalizeRisks(value) {
  return toList(value).map(normalizeRisk).filter(Boolean);
}
