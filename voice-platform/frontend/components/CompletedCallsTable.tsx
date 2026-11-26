'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, CheckCircle2, ChevronLeft, ChevronRight, Phone, Search } from 'lucide-react';
import { fetchCalls } from '@/lib/api';
import type { CallData } from '@/lib/api';
import { useDashboardStore } from '@/lib/store';
import { SecurityBadge } from '@/components/SecurityBadge';
import { ConversationView, agentLabel, cleanNumber, formatDuration, isHandedOver, topicLabel } from '@/components/ConversationView';
import { Badge, Button, Card, EmptyState, Page, Skeleton } from '@/components/ui/kit';

const PAGE_SIZE = 15;
type Outcome = 'all' | 'resolved' | 'handed';

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  // The server sends UTC without a 'Z'; add it, then show India time (IST) whatever zone the browser is in.
  const utc = iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z';
  const when = new Date(utc);
  if (Number.isNaN(when.getTime())) return '—';
  return when.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function CompletedCallsTable() {
  const { completedCalls, completedTotal, setCompletedCalls, refreshCounter, selectCall } = useDashboardStore();
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [outcome, setOutcome] = useState<Outcome>('all');
  const [openId, setOpenId] = useState<number | null>(null);   // the call being read, shown here instead of on the Live tab
  const openedRef = useRef(false);

  // Leaving History closes the call it opened, so the Live tab does not keep showing it.
  useEffect(() => () => { if (openedRef.current) selectCall(null); }, [selectCall]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const result = await fetchCalls('completed', PAGE_SIZE, page * PAGE_SIZE);
        if (!cancelled) setCompletedCalls(result.calls, result.total);
      } catch (err) {
        console.error('Failed to load completed calls:', err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [page, refreshCounter, setCompletedCalls]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return completedCalls.filter((c) => {
      if (outcome === 'resolved' && isHandedOver(c)) return false;
      if (outcome === 'handed' && !isHandedOver(c)) return false;
      if (!q) return true;
      return [cleanNumber(c.caller_number), cleanNumber(c.called_number), agentLabel(c.current_agent), c.intent, c.summary, c.latest_ticket?.ticket_id, c.latest_ticket?.issue]
        .some((v) => (v || '').toLowerCase().includes(q));
    });
  }, [completedCalls, query, outcome]);

  const open = (call: CallData) => { selectCall(call.id); openedRef.current = true; setOpenId(call.id); };
  const close = () => { selectCall(null); openedRef.current = false; setOpenId(null); };
  const totalPages = Math.max(1, Math.ceil(completedTotal / PAGE_SIZE));

  if (openId !== null) return <ConversationView onBack={close} backLabel="Back to call history" />;

  return (
    <Page
      width="max-w-7xl"
      title="Call history"
      subtitle="Every finished call. Open one to read the conversation."
      actions={
        <>
          <label className="relative">
            <Search className="w-4 h-4 text-stone-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search this page"
              aria-label="Search calls on this page"
              className="h-9 w-48 sm:w-56 pl-9 pr-3 rounded-lg border border-stone-300 dark:border-white/15 bg-white dark:bg-white/5 text-[13px] text-stone-900 dark:text-white placeholder-stone-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            />
          </label>
          <select
            value={outcome}
            onChange={(e) => setOutcome(e.target.value as Outcome)}
            aria-label="Filter by outcome"
            className="h-9 rounded-lg border border-stone-300 dark:border-white/15 bg-white dark:bg-[#12141c] px-2.5 text-[13px] text-stone-800 dark:text-stone-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            <option value="all">All outcomes</option>
            <option value="resolved">Resolved by the agent</option>
            <option value="handed">Handed to a person</option>
          </select>
        </>
      }
    >
      <Card padded={false}>
        {loading && completedCalls.length === 0 ? (
          <div className="p-5 space-y-3">{[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : shown.length === 0 ? (
          <EmptyState
            icon={<Phone className="w-5 h-5" strokeWidth={1.75} />}
            title={completedCalls.length === 0 ? 'No completed calls yet' : 'No calls match'}
            text={completedCalls.length === 0 ? 'Finished calls show up here, with their transcript and outcome.' : 'Nothing on this page matches. Try a different search or outcome, or look on another page.'}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-stone-200 dark:border-white/10 text-[12px] text-stone-500 dark:text-stone-400">
                  <th className="font-medium px-5 py-3">Caller</th>
                  <th className="font-medium px-3 py-3">When (IST)</th>
                  <th className="font-medium px-3 py-3">Length</th>
                  <th className="font-medium px-3 py-3">Outcome</th>
                  <th className="font-medium px-3 py-3">Topic</th>
                  <th className="font-medium px-3 py-3">Ticket</th>
                  <th className="font-medium px-3 py-3">Security</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100 dark:divide-white/5">
                {shown.map((call) => (
                  <tr key={call.id} onClick={() => open(call)} className="hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer">
                    <td className="px-5 py-3 text-[13px] font-medium text-stone-900 dark:text-white whitespace-nowrap">{cleanNumber(call.caller_number) || 'Unknown'}</td>
                    <td className="px-3 py-3 text-[13px] text-stone-500 dark:text-stone-400 whitespace-nowrap">{formatTime(call.started_at)}</td>
                    <td className="px-3 py-3 text-[13px] text-stone-600 dark:text-stone-300 tabular-nums">{formatDuration(call.duration_seconds)}</td>
                    <td className="px-3 py-3">
                      {isHandedOver(call)
                        ? <Badge tone="amber"><ArrowUpRight className="w-3 h-3" /> Handed over</Badge>
                        : call.status === 'completed'
                          ? <Badge tone="green"><CheckCircle2 className="w-3 h-3" /> Resolved</Badge>
                          : <Badge><span className="capitalize">{call.status}</span></Badge>}
                    </td>
                    <td className="px-3 py-3 max-w-[22rem]">
                      <p className="text-[13px] text-stone-800 dark:text-stone-100 truncate max-w-[22rem]">{topicLabel(call.intent) || topicLabel(call.latest_ticket?.intent) || 'No topic recorded'}</p>
                      <p className="text-[12px] text-stone-500 dark:text-stone-400 truncate max-w-[22rem]">
                        {call.latest_ticket?.issue || call.summary
                          || (call.duration_seconds !== null && call.duration_seconds <= 5 ? 'Caller hung up early' : 'No conversation recorded')}
                      </p>
                    </td>
                    <td className="px-3 py-3 text-[12px] font-mono text-stone-600 dark:text-stone-300 whitespace-nowrap">{call.latest_ticket?.ticket_id || '—'}</td>
                    <td className="px-3 py-3"><SecurityBadge security={call.security} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-[13px] text-stone-500 dark:text-stone-400">
          <span>Page {page + 1} of {totalPages}</span>
          <div className="flex gap-2">
            <Button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0}><ChevronLeft className="w-4 h-4" /> Previous</Button>
            <Button onClick={() => setPage(Math.min(totalPages - 1, page + 1))} disabled={page >= totalPages - 1}>Next <ChevronRight className="w-4 h-4" /></Button>
          </div>
        </div>
      )}
    </Page>
  );
}
