expect.extend({
  toBeEven(received) {
    return { pass: received % 2 === 0, message: () => `${received} is not even` };
  },
});
