const { add } = require('../../src/math.cjs');

test('CJS addition', () => expect(add(2, 2)).toBe(4));
