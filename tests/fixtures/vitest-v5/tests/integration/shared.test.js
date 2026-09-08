import { describe, expect, test } from 'vitest'
import { add } from '../../src/math.js'

describe('integration shared basename', () => {
  test('uses the same implementation', () => {
    expect(add(1, 3)).toBe(4)
  })
})
