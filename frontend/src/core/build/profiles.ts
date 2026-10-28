import type { BuildProfile, BuildProfileId } from '@/app/profile'
export type { BuildProfile, BuildProfileId } from '@/app/profile'

export const profiles: Record<BuildProfileId, BuildProfile> = {
  'universal-v1': { id: 'universal-v1', modules: ['prediction', 'records', 'datasets', 'knowledge', 'training', 'model_center', 'utilities', 'assistant', 'settings'], chromatographyTypes: ['tlc', 'cc', 'hplc', 'sfc', 'gc'], predictionTools: { tlc: ['single', 'condition_compare', 'reverse_solvent'], cc: ['single'], hplc: ['single'], sfc: ['single_column', 'full_column'], gc: ['single'] }, utilityFeatures: ['structure_converter', 'molecule_lookup', 'tlc_reverse_solvent'], assistantModes: ['knowledge', 'business', 'trace'] },
  'sfc-v1': { id: 'sfc-v1', modules: ['prediction', 'records', 'utilities'], chromatographyTypes: ['sfc'], predictionTools: { sfc: ['single_column', 'full_column'] }, utilityFeatures: ['structure_converter'], assistantModes: [] },
  'cc-v1': { id: 'cc-v1', modules: ['prediction', 'records', 'utilities'], chromatographyTypes: ['cc'], predictionTools: { cc: ['single'] }, utilityFeatures: ['structure_converter'], assistantModes: [] },
  'minimal-structure-v1': { id: 'minimal-structure-v1', modules: ['utilities'], chromatographyTypes: [], predictionTools: {}, utilityFeatures: ['structure_converter'], assistantModes: [] },
}

const requested = (typeof __BUILD_PROFILE__ === 'string' ? __BUILD_PROFILE__ : 'universal-v1') as BuildProfileId
export const activeProfile: BuildProfile = profiles[requested] ?? profiles['universal-v1']
