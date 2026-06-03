// Centralized backend API helper.
//
// All calls to the FastAPI backend go through here so the base URL and the
// X-API-Key auth header are applied in exactly one place. The key is attached
// only when VITE_API_KEY is set, so local/test environments without a key send
// no header (and the backend, if also keyless in local dev, can stay open).

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
const API_KEY = import.meta.env.VITE_API_KEY ?? '';

/**
 * POST a JSON body to a backend path and return the parsed JSON response.
 * Attaches `X-API-Key` when VITE_API_KEY is configured.
 * Throws `Error("HTTP <status>")` on a non-ok response so callers handle
 * failures in a single catch block.
 *
 * @param {string} path  Backend path beginning with "/", e.g. "/chat".
 * @param {object} body  JSON-serialisable request body.
 */
export async function apiPost(path, body) {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(API_KEY ? { 'X-API-Key': API_KEY } : {}),
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  return res.json();
}
