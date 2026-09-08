import { describe, expect, test } from 'vitest'
import { add } from '../../src/math.js'

describe('unit shared basename', () => {
  test('adds values', () => {
    expect(add(2, 2)).toBe(4)
  })
})
