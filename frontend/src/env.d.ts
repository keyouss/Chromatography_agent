/// <reference types="vite/client" />

declare const __BUILD_PROFILE__: string

interface ImportMetaEnv {
  readonly VITE_BUILD_PROFILE?: string
  readonly VITE_KETCHER_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
