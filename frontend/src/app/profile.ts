export type BuildProfileId = 'universal-v1' | 'sfc-v1' | 'cc-v1' | 'minimal-structure-v1'
export type ModuleId = 'prediction' | 'records' | 'datasets' | 'knowledge' | 'training' | 'model_center' | 'utilities' | 'assistant' | 'settings'
export type UtilityFeature = 'structure_converter' | 'molecule_lookup' | 'tlc_reverse_solvent'
export interface BuildProfile { id: BuildProfileId; modules: readonly ModuleId[]; chromatographyTypes: readonly string[]; predictionTools: Readonly<Record<string, readonly string[]>>; utilityFeatures: readonly UtilityFeature[]; assistantModes: readonly string[] }
