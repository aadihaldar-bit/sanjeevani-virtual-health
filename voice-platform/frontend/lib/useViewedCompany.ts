'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/adminApi';
import { getViewTenant } from '@/lib/tenantView';
import { useMe } from '@/lib/useMe';

/**
 * Whose workspace is on screen. A company's own people always see their company. A system administrator sees the
 * platform's own (STL) view for "All organizations" or STL, and a client's view once a client is picked.
 */
export function useViewedCompany(): { isOwnPlatform: boolean } {
  const { me } = useMe();
  const [orgs, setOrgs] = useState<{ id: number; is_system?: boolean }[] | null>(null);
  const picked = typeof window === 'undefined' ? null : getViewTenant();
  const needsList = !!me?.is_platform_staff && picked !== null;

  useEffect(() => {
    if (!needsList) return;
    let cancelled = false;
    api<{ orgs: { id: number; is_system?: boolean }[] }>('/api/tenants').then((r) => { if (!cancelled) setOrgs(r.orgs); }).catch(() => { if (!cancelled) setOrgs([]); });
    return () => { cancelled = true; };
  }, [needsList]);

  if (!me) return { isOwnPlatform: false };
  if (!me.is_platform_staff) return { isOwnPlatform: false };      // a client's people
  if (picked === null) return { isOwnPlatform: true };             // "All organizations"
  const chosen = orgs?.find((o) => String(o.id) === picked);
  return { isOwnPlatform: !!chosen?.is_system };
}
