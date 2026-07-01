/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_VAULT_API_URL?: string;
  readonly VITE_VAULT_API_PORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
