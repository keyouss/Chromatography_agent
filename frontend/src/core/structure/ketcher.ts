/** Accept only a same-origin relative path for the optional Ketcher frame. */
export function sameOriginKetcherUrl(value: string | undefined): string {
  const candidate = value?.trim() ?? ''
  if (!candidate || !candidate.startsWith('/') || candidate.startsWith('//') || candidate.includes('\\') || /^[a-z][a-z\d+.-]*:/i.test(candidate)) return ''
  return candidate
}

export const DEFAULT_KETCHER_URL = '/ketcher/index.html'
export function resolvedKetcherUrl(value: string | undefined): string {
  return sameOriginKetcherUrl(value) || DEFAULT_KETCHER_URL
}
