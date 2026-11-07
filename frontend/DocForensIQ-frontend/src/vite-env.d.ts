/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_SELF_REGISTRATION_ENABLED?: string;
  readonly VITE_USE_MOCK_API?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
