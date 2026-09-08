const doubled = (value: number): number => value * 2;

test('TypeScript transform', () => expect(doubled(4)).toBe(8));
