export interface KetcherEditor {
  subscribe(event: 'change', handler: (changes: unknown[]) => void): unknown
  unsubscribe(event: 'change', subscription: unknown): void
}

export interface KetcherApi {
  editor: KetcherEditor
  getSmiles(): string | Promise<string>
  setMolecule(smiles: string): void | Promise<void>
}

type KetcherWindow = Window & { ketcher?: KetcherApi }

export function ketcherApiFromFrame(frame: HTMLIFrameElement): KetcherApi | null {
  try {
    const url = new URL(frame.src, window.location.href)
    if (url.origin !== window.location.origin) return null
    return (frame.contentWindow as KetcherWindow | null)?.ketcher ?? null
  } catch {
    return null
  }
}

export function subscribeToKetcherChanges(api: KetcherApi, onChange: () => void): () => void {
  const subscription = api.editor.subscribe('change', onChange)
  return () => api.editor.unsubscribe('change', subscription)
}
