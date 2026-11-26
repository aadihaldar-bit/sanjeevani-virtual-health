'use client';

import { useEffect, useState } from 'react';
import { api, type Me } from '@/lib/adminApi';

// One request shared by every component that asks who the user is.
let cached: Promise<Me | null> | null = null;
function loadMe(): Promise<Me | null> {
  if (!cached) cached = api<Me>('/api/me').catch(() => null);      // an older backend has no /api/me
  return cached;
}

/** The signed-in user's role and organization (null while loading or when the server does not answer). */
export function useMe(): { me: Me | null; loading: boolean } {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    loadMe().then((m) => { if (!cancelled) { setMe(m); setLoading(false); } });
    return () => { cancelled = true; };
  }, []);
  return { me, loading };
}

/**
 * What this person's workspace should look like.
 *   isPlatform  STL staff or a super admin: sees the platform tools (speech tests, diagnostics, credits, STL branding)
 *   canManage   may create and change agents, knowledge and API keys
 *   canOperate  may work calls (end a call, create a ticket, use the simulator)
 *   workspaceName  the company name for a client's people, "STL Voice Agents" for the platform
 */
export function useWorkspace() {
  const { me, loading } = useMe();
  const isPlatform = !!me?.is_platform_staff;
  const canManage = isPlatform || !!me?.capabilities?.manage_agents_and_keys;
  const canOperate = isPlatform || !!me?.capabilities?.operate_calls;
  const workspaceName = isPlatform ? 'STL Voice Agents' : (me?.tenant_name || 'Your workspace');
  return { me, loading, isPlatform, canManage, canOperate, workspaceName };
}
