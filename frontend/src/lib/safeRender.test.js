import {
  normalizeKeyTerm,
  normalizeKeyTerms,
  normalizeRisks,
  normalizeTextList,
  toDisplayText,
  toList,
} from './safeRender';

describe('toDisplayText (LX-ENG-KT-01)', () => {
  test('primitives', () => {
    expect(toDisplayText('  Net 30 ')).toBe('Net 30');
    expect(toDisplayText(30)).toBe('30');
    expect(toDisplayText(NaN)).toBe('');
    expect(toDisplayText(true)).toBe('Yes');
    expect(toDisplayText(null)).toBe('');
    expect(toDisplayText(undefined)).toBe('');
    expect(toDisplayText(() => 1)).toBe('');
  });

  test('objects and arrays become plain text', () => {
    expect(toDisplayText({ label: 'Term', value: '12 months' })).toBe('Term: 12 months');
    expect(toDisplayText(['a', 2, null, { name: 'b' }])).toBe('a, 2, b');
    expect(toDisplayText({ foo: 'x', bar: { baz: 1 } })).toBe('foo: x; bar: baz: 1');
  });

  test('deep nesting and cycles do not throw', () => {
    const cyclic = { a: 'x' };
    cyclic.self = cyclic;
    expect(() => toDisplayText(cyclic)).not.toThrow();
    expect(toDisplayText({ a: { b: { c: { d: { e: 'deep' } } } } })).toBe('');
  });

  test('HTML-looking text stays text (rendered as a text child, never as markup)', () => {
    expect(toDisplayText('<img src=x onerror=alert(1)>')).toBe('<img src=x onerror=alert(1)>');
  });

  test('very long text is clipped', () => {
    expect(toDisplayText('x'.repeat(5000)).length).toBe(2000);
  });
});

describe('normalizeKeyTerm', () => {
  test.each([
    ['Net 30', { label: '', text: 'Net 30' }],
    [{ label: 'Payment', value: 'Net 30' }, { label: 'Payment', text: 'Net 30' }],
    [{ term: 'Renewal', definition: 'Auto-renews yearly' }, { label: 'Renewal', text: 'Auto-renews yearly' }],
    [{ name: 'Cap', value: 50000 }, { label: 'Cap', text: '50000' }],
    [{ title: 'Notice', description: { days: 30 } }, { label: 'Notice', text: 'days: 30' }],
    [{ term: 'Only a label' }, { label: '', text: 'Only a label' }],
    [{ value: ['a', 'b'] }, { label: '', text: 'a, b' }],
    [42, { label: '', text: '42' }],
  ])('%j', (input, expected) => {
    expect(normalizeKeyTerm(input)).toEqual(expected);
  });

  test('empty-ish values are dropped', () => {
    expect(normalizeKeyTerm(null)).toBeNull();
    expect(normalizeKeyTerm('')).toBeNull();
    expect(normalizeKeyTerm({})).toBeNull();
  });

  test('list helpers accept a non-array without throwing', () => {
    expect(toList('one')).toEqual(['one']);
    expect(toList(null)).toEqual([]);
    expect(normalizeKeyTerms('Net 30')).toEqual([{ label: '', text: 'Net 30' }]);
    expect(normalizeKeyTerms({ label: 'A', value: 'B' })).toEqual([{ label: 'A', text: 'B' }]);
    expect(normalizeTextList(['Do X', { text: 'Do Y' }, null, 7])).toEqual(['Do X', 'Do Y', '7']);
  });
});

describe('normalizeRisks', () => {
  test('mixed and malformed risks', () => {
    expect(
      normalizeRisks([
        { risk: 'Unlimited liability', severity: 'HIGH', recommendation: { text: 'Add a cap' } },
        'Loose string risk',
        { title: { label: 'Nested', value: 'title' }, level: 'low' },
        null,
        {},
      ]),
    ).toEqual([
      { risk: 'Unlimited liability', severity: 'high', recommendation: 'Add a cap' },
      { risk: 'Loose string risk', severity: null, recommendation: '' },
      { risk: 'Nested: title', severity: 'low', recommendation: '' },
    ]);
  });
});
