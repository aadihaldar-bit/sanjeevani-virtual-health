import { getIdToken } from "./useAuth";
import { getViewTenant } from "./tenantView";

/**
 * fetch() wrapper that attaches the current Firebase ID token as a Bearer
 * header, without disturbing any headers/body the caller already set
 * (e.g. FormData uploads that must not have Content-Type forced).
 */
export async function authFetch(url: string, options?: RequestInit): Promise<Response> {
  const token = await getIdToken();
  const headers = new Headers(options?.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const viewing = getViewTenant();            // a system admin looking at one company
  if (viewing) headers.set('X-Tenant-Id', viewing);
  return fetch(url, {
    ...options,
    headers,
  });
}
