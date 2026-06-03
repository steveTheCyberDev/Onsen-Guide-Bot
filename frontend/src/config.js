// Central frontend runtime config.
//
// All VITE_* environment reads and their dev-only fallbacks live here so values
// aren't hardcoded across components. To point the app at a different backend or
// rotate the API key, set the env vars (see .env.example) — never edit call sites.

// Dev fallback, used only when VITE_API_URL is unset (e.g. local dev without a
// .env). Production/preview must set VITE_API_URL to the deployed backend.
const DEFAULT_API_BASE_URL = 'http://localhost:8000';

export const API_BASE_URL = import.meta.env.VITE_API_URL ?? DEFAULT_API_BASE_URL;

// Shared secret sent as the X-API-Key header. Empty when unset, in which case
// the api helper omits the header (local dev / tests).
export const API_KEY = import.meta.env.VITE_API_KEY ?? '';

// Google Maps JavaScript API key for the map panel.
export const GOOGLE_MAPS_API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY ?? '';
