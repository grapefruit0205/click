test('stable snapshot input', () => {
  expect({ label: 'click', version: 1 }).toMatchSnapshot();
});
