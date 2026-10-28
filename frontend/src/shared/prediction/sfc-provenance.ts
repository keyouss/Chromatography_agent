import type { ContractSfcCandidateScopeProvenance } from '@/core/contract-types'

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function count(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= 100_000
}

/** Scope counts describe condition tuples, not experiments for the input molecule. */
export function isSfcScopeProvenance(value: unknown): value is ContractSfcCandidateScopeProvenance {
  if (!record(value) || Object.keys(value).some((key) => !['scope_id', 'policy', 'candidate_count', 'observed_count', 'unobserved_count'].includes(key))
    || typeof value.scope_id !== 'string' || !value.scope_id.trim() || value.scope_id.length > 96
    || !count(value.candidate_count) || value.candidate_count < 1
    || !count(value.observed_count) || !count(value.unobserved_count)
    || value.observed_count + value.unobserved_count !== value.candidate_count) return false
  const policy = value.policy
  return record(policy)
    && Object.keys(policy).length === 3
    && policy.expansion_mode === 'coupled_discrete_conditions' && policy.policy_version === '1'
    && typeof policy.candidate_source_sha256 === 'string' && /^[a-f0-9]{64}$/.test(policy.candidate_source_sha256)
}

/** Compare the immutable binding without depending on JSON property ordering. */
export function sameSfcScopeProvenance(left: unknown, right: unknown): boolean {
  if (left == null || right == null) return left == null && right == null
  return isSfcScopeProvenance(left) && isSfcScopeProvenance(right)
    && left.scope_id === right.scope_id && left.candidate_count === right.candidate_count
    && left.observed_count === right.observed_count && left.unobserved_count === right.unobserved_count
    && left.policy.expansion_mode === right.policy.expansion_mode
    && left.policy.policy_version === right.policy.policy_version
    && left.policy.candidate_source_sha256 === right.policy.candidate_source_sha256
}
