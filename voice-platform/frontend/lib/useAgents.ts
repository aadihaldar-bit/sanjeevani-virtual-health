'use client';

import { useEffect, useState } from 'react';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { useShellStore } from '@/lib/shellStore';

export interface AgentLite { id: number; name: string; is_active: boolean; }

/** The company's agents (id, name, which one is live), loaded once. Empty while loading or when there are none. */
export function useAgents(): { agents: AgentLite[]; loading: boolean } {
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [loading, setLoading] = useState(true);
  const version = useShellStore((s) => s.agentsVersion);       // refetch when an agent is created or removed
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch(`${getApiBase()}/api/agents/`);
        if (!res.ok) return;
        const data = await res.json();
        const list: AgentLite[] = (Array.isArray(data) ? data : data.agents || [])
          .map((w: any) => ({ id: w.id, name: w.name, is_active: !!w.is_active }));
        if (!cancelled) setAgents(list);
      } catch { /* the shell works without it */ }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [version]);
  return { agents, loading };
}
