'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowRight, CheckCircle2, Download, Plus, RefreshCw, Search, Ticket } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import { getApiBase } from '@/lib/api';
import { authFetch } from '@/lib/authFetch';
import { useWorkspace } from '@/lib/useMe';
import { Badge, Button, Card, EmptyState, Page, Skeleton, StatCard, timeAgo } from '@/components/ui/kit';
import { Dialog, FormField, inputClass } from '@/components/ui/Dialog';

interface TicketRow {
  id: number;
  ticket_id: string;
  call_id: number | null;
  caller_number: string;
  intent: string;
  issue: string;
  priority: string;
  transcript_summary: string | null;
  sentiment: string | null;
  created_at: string;
}

const PRIORITIES = ['P1 - Critical', 'P2 - High', 'P3 - Medium', 'P4 - Low'];
const FILTERS = ['ALL', 'P1', 'P2', 'P3', 'P4'];

function level(priority: string): 1 | 2 | 3 | 4 {
  const p = (priority || '').toLowerCase();
  if (p.includes('p1') || p.includes('critical')) return 1;
  if (p.includes('p2') || p.includes('high')) return 2;
  if (p.includes('p4') || p.includes('low')) return 4;
  return 3;
}

const TONE = { 1: 'red', 2: 'amber', 3: 'blue', 4: 'grey' } as const;
const LEVEL_LABEL = { 1: 'P1 - Critical', 2: 'P2 - High', 3: 'P3 - Medium', 4: 'P4 - Low' } as const;

/** One label per level: tickets arrive as "P1", "P1-Critical" and "critical" (and so on), which are all the same urgency. */
function priorityLabel(priority: string): string {
  const p = (priority || '').trim();
  return !p || /p[1-4]|critical|high|medium|low/i.test(p) ? LEVEL_LABEL[level(p)] : p;
}

/** The topic is the call's intent: it can be missing, "unknown" or snake_case ("network_printer"). */
function topicLabel(intent?: string | null): string {
  const s = (intent || '').trim().replace(/_/g, ' ');
  if (!s || s.toLowerCase() === 'unknown') return 'General';
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Timestamps are stored in UTC without a zone marker; the console shows India time. */
function istText(iso?: string | null, opts: Intl.DateTimeFormatOptions = {}, locale = 'en-IN'): string {
  if (!iso) return '';
  const d = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : iso + 'Z');
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(locale, { timeZone: 'Asia/Kolkata', ...opts });
}

/** Hover text for "56 d ago": the exact date, since a count of days is no use for finding a ticket. */
function createdTitle(iso?: string | null): string | undefined {
  const when = istText(iso, { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  return when ? `${when} IST` : undefined;
}

export function TicketsPanel() {
  const { setSelectedTab, selectCall } = useDashboardStore();
  const { canOperate } = useWorkspace();
  const [tickets, setTickets] = useState<TicketRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('ALL');
  const [open, setOpen] = useState<TicketRow | null>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      // The server sends 100 by default; ask for the most it allows so the count matches what the company really has.
      const res = await authFetch(`${getApiBase()}/api/tickets?limit=500`);
      if (!res.ok) { setError(`The server answered ${res.status}.`); setTickets([]); return; }
      const data = await res.json();
      setTickets(Array.isArray(data) ? data : []);
    } catch {
      setError('Could not reach the server.');
      setTickets([]);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (tickets ?? []).filter((t) => {
      if (filter !== 'ALL' && `P${level(t.priority)}` !== filter) return false;
      return !q || [t.ticket_id, t.issue, t.caller_number, t.intent].some((v) => (v || '').toLowerCase().includes(q));
    });
  }, [tickets, search, filter]);

  const count = (n: number) => (tickets ?? []).filter((t) => level(t.priority) === n).length;

  function exportCSV() {
    // Same wording and India time as the table, so a sheet built from the export lines up with the screen.
    const stamp = (iso: string) => istText(iso, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }, 'sv-SE') || iso;
    const rows = [['Ticket', 'Topic', 'Priority', 'Issue', 'Caller', 'Created (IST)'], ...shown.map((t) => [t.ticket_id, topicLabel(t.intent), priorityLabel(t.priority), t.issue, t.caller_number, stamp(t.created_at)])];
    const csv = rows.map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\r\n');
    const link = document.createElement('a');
    // The leading byte-order mark makes Excel read the file as UTF-8 instead of garbling non-English text.
    link.href = URL.createObjectURL(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8;' }));
    link.download = `tickets-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  return (
    <Page
      width="max-w-7xl"
      title="Tickets"
      subtitle="Issues your agents raised during calls, and ones you create yourself."
      actions={
        <>
          <Button onClick={load} aria-label="Refresh"><RefreshCw className="w-4 h-4" /></Button>
          <Button onClick={exportCSV} disabled={shown.length === 0}><Download className="w-4 h-4" /> Export</Button>
          {canOperate && <Button variant="primary" onClick={() => setCreating(true)}><Plus className="w-4 h-4" /> New ticket</Button>}
        </>
      }
    >
      {notice && (
        <p role="status" className="flex items-center gap-2 rounded-lg border border-emerald-200 dark:border-emerald-500/20 bg-emerald-50 dark:bg-emerald-500/10 px-4 py-2.5 text-[13px] text-emerald-800 dark:text-emerald-300">
          <CheckCircle2 className="w-4 h-4" /> {notice}
        </p>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="All tickets" value={tickets?.length ?? 0} loading={tickets === null} />
        <StatCard label="Critical (P1)" value={count(1)} hint="needs attention first" loading={tickets === null} />
        <StatCard label="High (P2)" value={count(2)} loading={tickets === null} />
        <StatCard label="Medium and low" value={count(3) + count(4)} loading={tickets === null} />
      </div>

      <Card padded={false}>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-4 border-b border-stone-100 dark:border-white/5">
          <label className="relative sm:max-w-xs flex-1">
            <Search className="w-4 h-4 text-stone-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search tickets, issues, callers" aria-label="Search tickets" className={`${inputClass} pl-9`} />
          </label>
          <div role="group" aria-label="Priority" className="flex gap-1.5 flex-wrap">
            {FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                aria-pressed={filter === f}
                className={`h-8 px-3 rounded-full text-[12px] font-medium cursor-pointer transition-colors ${filter === f ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900' : 'bg-stone-100 dark:bg-white/5 text-stone-600 dark:text-stone-300 hover:bg-stone-200 dark:hover:bg-white/10'}`}
              >
                {f === 'ALL' ? 'All priorities' : f}
              </button>
            ))}
          </div>
        </div>

        {tickets === null ? (
          <div className="p-5 space-y-3">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : error ? (
          <EmptyState icon={<Ticket className="w-5 h-5" strokeWidth={1.75} />} title="Could not load tickets" text={error} action={<Button onClick={load}>Try again</Button>} />
        ) : shown.length === 0 ? (
          <EmptyState
            icon={<Ticket className="w-5 h-5" strokeWidth={1.75} />}
            title={(tickets?.length ?? 0) === 0 ? 'No tickets yet' : 'No tickets match'}
            text={(tickets?.length ?? 0) === 0
              ? 'When an agent raises an issue during a call it shows up here. You can also create one by hand.'
              : 'Try a different search or priority.'}
            action={(tickets?.length ?? 0) === 0 && canOperate ? <Button variant="primary" onClick={() => setCreating(true)}><Plus className="w-4 h-4" /> New ticket</Button> : undefined}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-stone-200 dark:border-white/10 text-[12px] text-stone-500 dark:text-stone-400">
                  <th className="font-medium px-5 py-3">Ticket</th>
                  <th className="font-medium px-3 py-3">Topic</th>
                  <th className="font-medium px-3 py-3">Issue</th>
                  <th className="font-medium px-3 py-3">Priority</th>
                  <th className="font-medium px-3 py-3">Caller</th>
                  <th className="font-medium px-3 py-3">Created</th>
                  <th className="px-5 py-3"><span className="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100 dark:divide-white/5">
                {shown.map((t) => (
                  <tr key={t.id} className="hover:bg-stone-50 dark:hover:bg-white/5">
                    <td className="px-5 py-3 text-[13px] font-mono font-medium text-stone-900 dark:text-white whitespace-nowrap">{t.ticket_id}</td>
                    <td className="px-3 py-3 text-[13px] text-stone-600 dark:text-stone-300 max-w-[14rem] truncate" title={t.intent || undefined}>{topicLabel(t.intent)}</td>
                    <td className="px-3 py-3 text-[13px] text-stone-800 dark:text-stone-100 max-w-sm truncate" title={t.issue}>{t.issue}</td>
                    <td className="px-3 py-3"><Badge tone={TONE[level(t.priority)]}>{priorityLabel(t.priority)}</Badge></td>
                    <td className="px-3 py-3 text-[12px] font-mono text-stone-600 dark:text-stone-400 max-w-[12rem] truncate" title={t.caller_number || undefined}>{t.caller_number || '—'}</td>
                    <td className="px-3 py-3 text-[12px] text-stone-500 dark:text-stone-400 whitespace-nowrap" title={createdTitle(t.created_at)}>{timeAgo(t.created_at)}</td>
                    <td className="px-5 py-3 text-right whitespace-nowrap">
                      {t.call_id ? (
                        <Button variant="ghost" className="h-8 px-2.5" onClick={() => { selectCall(t.call_id); setSelectedTab('live'); }}>View call <ArrowRight className="w-3.5 h-3.5" /></Button>
                      ) : (
                        <Button variant="ghost" className="h-8 px-2.5" onClick={() => setOpen(t)}>Details</Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <TicketDetails ticket={open} onClose={() => setOpen(null)} />
      <NewTicket
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(id) => { setCreating(false); setNotice(`Ticket ${id} created.`); void load(); setTimeout(() => setNotice(null), 8000); }}
      />
    </Page>
  );
}

function TicketDetails({ ticket, onClose }: { ticket: TicketRow | null; onClose: () => void }) {
  return (
    <Dialog open={!!ticket} title={ticket?.ticket_id || ''} onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      {ticket && (
        <dl className="space-y-4 pb-2 text-[13px]">
          <div><dt className="text-[12px] text-stone-500">Issue</dt><dd className="mt-0.5 text-stone-900 dark:text-white break-words">{ticket.issue}</dd></div>
          <div className="grid grid-cols-2 gap-4">
            <div><dt className="text-[12px] text-stone-500">Priority</dt><dd className="mt-0.5"><Badge tone={TONE[level(ticket.priority)]}>{priorityLabel(ticket.priority)}</Badge></dd></div>
            <div><dt className="text-[12px] text-stone-500">Topic</dt><dd className="mt-0.5 text-stone-900 dark:text-white break-words">{topicLabel(ticket.intent)}</dd></div>
          </div>
          <div><dt className="text-[12px] text-stone-500">Reported by</dt><dd className="mt-0.5 font-mono text-stone-900 dark:text-white break-all">{ticket.caller_number || '—'}</dd></div>
          {ticket.transcript_summary && <div><dt className="text-[12px] text-stone-500">Notes</dt><dd className="mt-0.5 text-stone-700 dark:text-stone-300 whitespace-pre-line break-words">{ticket.transcript_summary}</dd></div>}
        </dl>
      )}
    </Dialog>
  );
}

function NewTicket({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const [reporter, setReporter] = useState('');
  const [topic, setTopic] = useState('');
  const [issue, setIssue] = useState('');
  const [priority, setPriority] = useState(PRIORITIES[2]);
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) { setReporter(''); setTopic(''); setIssue(''); setPriority(PRIORITIES[2]); setNotes(''); setError(null); }
  }, [open]);

  const valid = reporter.trim().length > 0 && issue.trim().length >= 5;

  async function submit() {
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await authFetch(`${getApiBase()}/api/tickets/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          caller_number: reporter.trim(),
          intent: topic.trim() || 'General',
          issue: issue.trim(),
          priority,
          transcript_summary: notes.trim() || issue.trim(),
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) { setError(typeof data?.detail === 'string' ? data.detail : `Could not create the ticket (${res.status}).`); return; }
      onCreated(data?.ticket_id || 'new ticket');
    } catch {
      setError('Could not reach the server.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      title="New ticket"
      description="Log an issue by hand. It appears in the list straight away."
      onClose={onClose}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={submit} disabled={!valid || busy}>{busy ? 'Creating…' : 'Create ticket'}</Button></>}
    >
      <div className="space-y-4 pb-2">
        <FormField label="Reported by" required hint="A phone number or a name."><input className={inputClass} value={reporter} onChange={(e) => setReporter(e.target.value)} placeholder="+91 98765 43210" /></FormField>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <FormField label="Topic" hint="For example Network, Billing, Hardware."><input className={inputClass} value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="General" /></FormField>
          <FormField label="Priority"><select className={inputClass} value={priority} onChange={(e) => setPriority(e.target.value)}>{PRIORITIES.map((p) => <option key={p}>{p}</option>)}</select></FormField>
        </div>
        <FormField label="What is the issue?" required hint="At least a few words."><textarea className={`${inputClass} h-24`} value={issue} onChange={(e) => setIssue(e.target.value)} placeholder="Describe what went wrong and who it affects." /></FormField>
        <FormField label="Notes" hint="Optional extra detail."><textarea className={`${inputClass} h-16`} value={notes} onChange={(e) => setNotes(e.target.value)} /></FormField>
        {error && <p role="alert" className="text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
      </div>
    </Dialog>
  );
}
