/**
 * Which company a system admin is looking at. Everyone else is held to their own company by the server, so this only
 * ever has an effect for super admins: the value is sent as X-Tenant-Id and the server narrows every list to it.
 * null means "all organizations".
 */
const KEY = 'stl_view_tenant';

export function getViewTenant(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** Remember the choice and reload so every screen refetches under it. */
export function setViewTenant(id: number | null): void {
  try {
    if (id === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, String(id));
  } catch { /* private mode: the choice just does not stick */ }
  if (typeof window !== 'undefined') window.location.reload();
}
