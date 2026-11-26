'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, ArrowUpRight, Bot, ChevronRight, Phone, RefreshCw } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import { fetchCalls, CallData, getApiBase } from '@/lib/api';
import { authFetch } from '@/lib/authFetch';
import { agentLabel, cleanNumber, isHandedOver } from '@/components/ConversationView';
import { Badge, Button, Card, EmptyState, Page, Skeleton, timeAgo } from '@/components/ui/kit';

interface LiveAgent { id?: number; name: string; node_count: number; }

/** "25s", "2m 05s". Durations arrive as floats (24.76214), and finished calls can have 0 or none recorded. */
function spoken(seconds: number): string {
  const total = Math.round(seconds);
  return total < 60 ? `${total}s` : `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, '0')}s`;
}

/** The quiet line under a call: how long it ran, which agent had it, how the caller felt. */
function detailLine(call: CallData): string {
  const length = call.status === 'active' ? 'In progress' : call.duration_seconds ? spoken(call.duration_seconds) : '';
  const sentiment = call.sentiment ? call.sentiment.charAt(0).toUpperCase() + call.sentiment.slice(1) : '';
  return [length, agentLabel(call.current_agent), sentiment].filter(Boolean).join(' · ') || 'No details recorded';
}

/** What is answering the phones right now, and the latest calls. Click a call to read it. */
export function LiveOperationsCenter() {
  const { selectCall } = useDashboardStore();
  const [agent, setAgent] = useState<LiveAgent | null | undefined>(undefined);   // undefined = loading, null = none is live
  const [calls, setCalls] = useState<CallData[] | null>(null);

  const load = async () => {
    try {
      const wfRes = await authFetch(`${getApiBase()}/api/workflows/active`);
      const wf = wfRes.ok ? await wfRes.json() : null;
      setAgent(wf ? { id: wf.id, name: wf.name, node_count: wf.nodes ? wf.nodes.length : 0 } : null);
      const res = await fetchCalls(undefined, 8, 0);
      setCalls(res?.calls ?? []);
    } catch {
      setAgent((a) => (a === undefined ? null : a));
      setCalls((c) => c ?? []);
    }
  };

  useEffect(() => {
    void load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);

  return (
    <Page
      width="max-w-4xl"
      title="Live"
      subtitle="The agent answering your calls, and the latest conversations."
      actions={<Button onClick={load} aria-label="Refresh"><RefreshCw className="w-4 h-4" /></Button>}
    >
      <Card padded={false}>
        {agent === undefined ? (
          <div className="p-5"><Skeleton className="h-10" /></div>
        ) : agent === null ? (
          <EmptyState icon={<Bot className="w-5 h-5" strokeWidth={1.75} />} title="No agent is live" text="Make an agent live from the Agents page and it starts answering calls to your numbers." />
        ) : (
          <div className="flex items-center gap-4 p-5">
            <span className="relative w-10 h-10 rounded-lg bg-emerald-50 dark:bg-emerald-500/10 flex items-center justify-center text-emerald-600 flex-shrink-0">
              <Bot className="w-5 h-5" strokeWidth={1.75} />
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-emerald-500 ring-2 ring-white dark:ring-[#0f121a]" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[12px] text-stone-500 dark:text-stone-400">Live agent</p>
              <p className="text-[14px] font-semibold text-stone-900 dark:text-white truncate">{agent.name}</p>
              {agent.node_count > 0 && <p className="text-[12px] text-stone-500 dark:text-stone-400">{agent.node_count} steps</p>}
            </div>
            <Link href={agent.id ? `/workflow?id=${agent.id}` : '/workflow'} className="inline-flex items-center gap-1.5 h-9 px-3.5 rounded-lg border border-stone-300 dark:border-white/15 text-[13px] font-medium text-stone-800 dark:text-stone-100 hover:bg-stone-50 dark:hover:bg-white/10">
              Open builder <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        )}
      </Card>

      <Card title="Latest calls" padded={false}>
        {calls === null ? (
          <div className="p-5 pt-3 space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : calls.length === 0 ? (
          <EmptyState icon={<Phone className="w-5 h-5" strokeWidth={1.75} />} title="No calls yet" text="Calls to your numbers, and test calls from the simulator, appear here as they happen." />
        ) : (
          <ul className="divide-y divide-stone-100 dark:divide-white/5 mt-3">
            {calls.map((call) => (
              <li key={call.id}>
                <button onClick={() => selectCall(call.id)} className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer">
                  <span className="w-8 h-8 rounded-full bg-stone-100 dark:bg-white/5 flex items-center justify-center text-stone-500 flex-shrink-0"><Phone className="w-3.5 h-3.5" strokeWidth={1.75} /></span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] font-medium text-stone-900 dark:text-white truncate">{cleanNumber(call.caller_number) || 'Unknown caller'}</p>
                    <p className="text-[12px] text-stone-500 dark:text-stone-400 truncate">{detailLine(call)}</p>
                  </div>
                  {isHandedOver(call) && <Badge tone="amber"><ArrowUpRight className="w-3 h-3" /> Handed over</Badge>}
                  <Badge tone={call.status === 'completed' ? 'green' : call.status === 'escalated' ? 'amber' : 'grey'}><span className="capitalize">{call.status}</span></Badge>
                  <span className="hidden sm:block text-[12px] text-stone-400 tabular-nums w-20 text-right">{timeAgo(call.started_at)}</span>
                  <ChevronRight className="w-4 h-4 text-stone-300" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Page>
  );
}
