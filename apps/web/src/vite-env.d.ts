/// <reference types="vite/client" />

/**
 * The three build-time settings the app reads, typed.
 *
 * `import.meta.env` is otherwise `Record<string, string | undefined>` with
 * `any` semantics for unknown keys, which is how a typo in a variable name
 * ships silently. Declaring them makes `VITE_REVI_DIRVER` a type error.
 *
 * All three are OPTIONAL, and every read site has a default — the app runs
 * against `http://localhost:8000` as tenant `demo` on the live driver with
 * no env file at all, which is what `.env.example` documents.
 */
interface ImportMetaEnv {
  /** `mock` forces the golden-conversation replay; anything else is the live API. */
  readonly VITE_REVI_DRIVER?: string;
  /** Revi API origin (api mode). Defaults to `http://localhost:8000`. */
  readonly VITE_REVI_API_URL?: string;
  /** Tenant sent on `POST /v1/sessions`. Defaults to `demo`. */
  readonly VITE_REVI_TENANT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
