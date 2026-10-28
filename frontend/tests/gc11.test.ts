import { describe, expect, it } from 'vitest'
import { createGc11Selection, isGc11Input, isGc11StructureRefs } from '@/shared/prediction/gc11'
import { gcFormCapabilityFromSchema, gcFormValueError } from '@/modules/prediction/forms/basic/gc-temperature-program'
import type { Gc11ModelOption } from '@/shared/prediction/gc11'

const hash = 'a'.repeat(64)
function gcModel(overrides: Record<string, unknown> = {}): Gc11ModelOption {
  return {
    id: 'gc-model', version: 'model-v1', task_type: 'gc', scope: 'gc-retention',
    input_schema_version: '1.1', output_schema_version: 'gc-retention-time-1',
    preprocessing_profile_id: 'gc-profile-v1', preprocessing_profile_sha256: hash,
    health: { status: 'READY', reason: null, checked_at: '2026-10-07T00:00:00Z' },
    ...overrides,
  } as Gc11ModelOption
}

function numeric(extra: Record<string, unknown> = {}) { return { type: 'number', ...extra } }
function capabilitySchema(seriesMin = 4, seriesMax = 4) {
  return {
    $defs: {
      GcLinearTemperatureProgram: {
        type: 'object', additionalProperties: false,
        properties: {
          mode: { const: 'linear' },
          initial_temperature_c: numeric({ minimum: 30, maximum: 300 }),
          final_temperature_c: numeric({ minimum: 30, maximum: 400 }),
          heating_rate_c_per_min: numeric({ exclusiveMinimum: 0, maximum: 100 }),
          initial_hold_min: numeric({ minimum: 0, maximum: 100, multipleOf: 1 }),
        },
        required: ['mode', 'initial_temperature_c', 'final_temperature_c', 'heating_rate_c_per_min', 'initial_hold_min'],
      },
      GcSeriesTemperatureProgram: {
        type: 'object', additionalProperties: false,
        properties: { mode: { const: 'series' }, temperature_series_c: { type: 'array', minItems: seriesMin, maxItems: seriesMax, items: numeric() } },
        required: ['mode', 'temperature_series_c'],
      },
    },
    properties: {
      input: {
        type: 'object', additionalProperties: false, required: ['temperature_program'],
        properties: { temperature_program: { oneOf: [
          { $ref: '#/$defs/GcLinearTemperatureProgram' },
          { $ref: '#/$defs/GcSeriesTemperatureProgram' },
        ] } },
      },
    },
  }
}
function modelConstraints(tempPoints = 4) {
  return { model_id: 'gc-model', business_scope_id: 'gc-retention', temperature_points: tempPoints }
}

describe('GC 1.1 candidate boundary', () => {
  it('requires a READY 1.1 model with the observed complete profile pair', () => {
    expect(createGc11Selection(gcModel())).toEqual({
      mode: 'exact', model_id: 'gc-model', model_version: 'model-v1',
      preprocessing_profile_id: 'gc-profile-v1', preprocessing_profile_sha256: hash,
    })
    expect(() => createGc11Selection(gcModel({ input_schema_version: '1.0' }))).toThrow('1.1')
    expect(() => createGc11Selection(gcModel({ preprocessing_profile_id: null }))).toThrow('完整')
    expect(() => createGc11Selection(gcModel({ preprocessing_profile_sha256: 'A'.repeat(64) }))).toThrow('完整')
  })

  it('accepts exactly one contract-valid structure ref and rejects zero, two, or unsafe refs', () => {
    expect(isGc11StructureRefs(['structure-1'])).toBe(true)
    expect(isGc11StructureRefs([])).toBe(false)
    expect(isGc11StructureRefs(['structure-1', 'structure-2'])).toBe(false)
    expect(isGc11StructureRefs(['../structure'])).toBe(false)
  })

  it('validates GC 1.1 input shape while leaving model-specific integer rules to capability schema', () => {
    expect(isGc11Input({ temperature_program: { mode: 'linear', initial_temperature_c: 50, final_temperature_c: 50, heating_rate_c_per_min: 10, initial_hold_min: 0.5 } })).toBe(true)
    expect(isGc11Input({ temperature_program: { mode: 'linear', initial_temperature_c: 50, final_temperature_c: 49, heating_rate_c_per_min: 10, initial_hold_min: 0 } })).toBe(false)
    expect(isGc11Input({ temperature_program: { mode: 'series', temperature_series_c: [40, 50, 60, 70] } })).toBe(true)
    expect(isGc11Input({ temperature_program: { mode: 'series', temperature_series_c: [] } })).toBe(false)
  })

  it('derives model-specific modes, numeric bounds, multipleOf, and series length from READY schema', () => {
    const capability = gcFormCapabilityFromSchema(capabilitySchema(4, 4), modelConstraints(), { id: 'gc-model', scope: 'gc-retention' })
    expect(capability?.series).toEqual({ minItems: 4, maxItems: 4 })
    expect(capability?.initial_hold_min.multipleOf).toBe(1)
    expect(gcFormValueError({ temperature_program: { mode: 'series', temperature_series_c: [40, 50, 60, 70] } }, capability)).toBeNull()
    expect(gcFormValueError({ temperature_program: { mode: 'series', temperature_series_c: [40, 50, 60] } }, capability)).toContain('4')
    expect(gcFormValueError({ temperature_program: { mode: 'linear', initial_temperature_c: 50, final_temperature_c: 50, heating_rate_c_per_min: 10, initial_hold_min: 0.5 } }, capability)).toContain('倍数')
  })

  it('fails closed for incomplete/placeholder schemas and mismatched model constraints', () => {
    expect(gcFormCapabilityFromSchema(undefined, modelConstraints(), { id: 'gc-model', scope: 'gc-retention' })).toBeNull()
    expect(gcFormCapabilityFromSchema({ properties: { input: { properties: { temperature_program: { modes: ['linear', 'series'], series_length: 30 } } } } }, modelConstraints(), { id: 'gc-model', scope: 'gc-retention' })).toBeNull()
    expect(gcFormCapabilityFromSchema(capabilitySchema(), { ...modelConstraints(), model_id: 'other-model' }, { id: 'gc-model', scope: 'gc-retention' })).toBeNull()
    expect(gcFormCapabilityFromSchema(capabilitySchema(), modelConstraints(), { id: 'gc-model', scope: 'other-scope' })).toBeNull()
    expect(gcFormCapabilityFromSchema(capabilitySchema(), modelConstraints(30), { id: 'gc-model', scope: 'gc-retention' })).toBeNull()
  })
})
