const { add } = require('../../src/math.cjs');

test('setup matcher', () => expect(add(3, 3)).toBeEven());
