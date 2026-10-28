import { describe, expect, it } from 'vitest'
import { hplcColumnOptions } from '@/modules/prediction/forms/basic/form-types'

const serverColumns = ['ADH', 'ODH', 'IC', 'IA', 'OJH', 'ASH', 'IC3', 'IE', 'ID', 'OD3', 'IB', 'AD', 'AD3', 'IF', 'OD', 'AS', 'OJ3', 'IG', 'AZ', 'IAH', 'OJ', 'ICH', 'OZ3', 'IF3', 'IAU']

describe('HPLC column options from tool capability constraints', () => {
  it('maps the server-declared 25-column constraint without changing identifiers or order', () => {
    expect(hplcColumnOptions({ columns: serverColumns })).toEqual(serverColumns.map((column) => ({ value: column, label: column })))
  })

  it('fails closed for absent, empty, or malformed column constraints', () => {
    expect(hplcColumnOptions(undefined)).toEqual([])
    expect(hplcColumnOptions({})).toEqual([])
    expect(hplcColumnOptions({ columns: [] })).toEqual([])
    expect(hplcColumnOptions({ columns: ['ADH', ''] })).toEqual([])
    expect(hplcColumnOptions({ columns: ['ADH', 2] })).toEqual([])
    expect(hplcColumnOptions({ columns: ['ADH', 'ADH'] })).toEqual([])
  })
})
