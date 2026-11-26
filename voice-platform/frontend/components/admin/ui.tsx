'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, X } from 'lucide-react';
import { api, ApiError, type Me, type Org } from '@/lib/adminApi';
import { getViewTenant } from '@/lib/tenantView';

export const inputCls =
  'w-full min-h-[2.25rem] px-3 py-1.5 rounded-lg text-[13px] bg-white dark:bg-white/5 border border-stone-300 dark:border-white/15 text-stone-900 dark:text-white ' +
  'placeholder:text-stone-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-50';

export function Card({ title, subtitle, actions, children }: {
  title: string; subtitle?: string; actions?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a]">
      <header className="flex items-start justify-between gap-3 px-5 pt-4">
        <div className="min-w-0">
          <h3 className="text-[14px] font-semibold text-stone-900 dark:text-white">{title}</h3>
          {subtitle && <p className="text-[12px] text-stone-500 dark:text-stone-400 mt-0.5 max-w-2xl">{subtitle}</p>}
        </div>
        {actions && <div className="flex items-center gap-2 flex-shrink-0">{actions}</div>}
      </header>
      <div className="p-5 pt-4">{children}</div>
    </section>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-[12px] font-medium text-stone-700 dark:text-stone-300 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-stone-400 mt-1">{hint}</span>}
    </label>
  );
}

export function Btn({ children, onClick, kind = 'primary', disabled, busy, type = 'button', title }: {
  children: React.ReactNode; onClick?: () => void; kind?: 'primary' | 'ghost' | 'danger'; disabled?: boolean; busy?: boolean;
  type?: 'button' | 'submit'; title?: string;
}) {
  const style = {
    primary: 'bg-indigo-600 text-white hover:bg-indigo-500 border border-transparent',
    ghost: 'border border-stone-300 dark:border-white/15 bg-white dark:bg-white/5 text-stone-800 dark:text-stone-100 hover:bg-stone-50 dark:hover:bg-white/10',
    danger: 'border border-rose-200 dark:border-rose-500/30 text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-500/10',
  }[kind];
  return (
    <button type={type} title={title} onClick={onClick} disabled={disabled || busy}
      className={`inline-flex items-center justify-center gap-1.5 h-9 px-3.5 rounded-lg text-[13px] font-medium transition-colors cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed ${style}`}>
      {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
      {children}
    </button>
  );
}

const BADGE: Record<string, string> = {
  green: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900/60',
  amber: 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60',
  red: 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-900/60',
  grey: 'bg-stone-100 text-stone-600 border-stone-200 dark:bg-white/5 dark:text-stone-300 dark:border-white/10',
  blue: 'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-900/60',
};

export function Badge({ children, tone = 'grey' }: { children: React.ReactNode; tone?: keyof typeof BADGE }) {
  return <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ${BADGE[tone]}`}>{children}</span>;
}

export function Notice({ kind, children, onClose }: { kind: 'error' | 'success' | 'info'; children: React.ReactNode; onClose?: () => void }) {
  const tone = {
    error: 'bg-rose-50 dark:bg-rose-950/40 border-rose-200 dark:border-rose-900/60 text-rose-800 dark:text-rose-200',
    success: 'bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-900/60 text-emerald-800 dark:text-emerald-200',
    info: 'bg-sky-50 dark:bg-sky-950/40 border-sky-200 dark:border-sky-900/60 text-sky-800 dark:text-sky-200',
  }[kind];
  const Icon = kind === 'success' ? CheckCircle2 : AlertTriangle;
  return (
    <div role={kind === 'error' ? 'alert' : 'status'} className={`flex items-start gap-2 rounded-lg border px-3.5 py-2.5 text-[13px] mb-4 ${tone}`}>
      <Icon className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <div className="flex-1 min-w-0 break-words">{children}</div>
      {onClose && <button onClick={onClose} aria-label="Dismiss" className="opacity-60 hover:opacity-100 cursor-pointer"><X className="w-3.5 h-3.5" /></button>}
    </div>
  );
}

export function Table({ head, children, empty }: { head: string[]; children: React.ReactNode; empty?: string }) {
  const rows = React.Children.count(children);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="text-left text-[12px] text-stone-500 dark:text-stone-400 border-b border-stone-200 dark:border-white/10">
            {head.map((h) => <th key={h} className="py-2.5 pr-4 font-medium whitespace-nowrap">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-100 dark:divide-white/5 text-stone-700 dark:text-stone-200">{children}</tbody>
      </table>
      {rows === 0 && <p className="text-[13px] text-stone-400 py-8 text-center">{empty || 'Nothing here yet.'}</p>}
    </div>
  );
}

/** Runs a server action, shows what went wrong in the server's own words, and exposes a busy flag. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const run = useCallback(async <T,>(fn: () => Promise<T>, success?: string): Promise<T | undefined> => {
    setBusy(true); setError(null); setDone(null);
    try {
      const out = await fn();
      if (success) setDone(success);
      return out;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong. Please try again.');
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, done, run, clear: () => { setError(null); setDone(null); } };
}

/**
 * Which organization the admin screens act on. Tenant admins are fixed to their own;
 * platform admins choose from every organization.
 */
export function useOrgScope(me: Me) {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [orgId, setOrgId] = useState<number | null>(me.tenant_id);
  const reload = useCallback(async () => {
    try {
      const data = await api<{ orgs: Org[] }>('/api/tenants');
      setOrgs(data.orgs);
      const viewing = Number(getViewTenant());                       // the company picked in the top bar, if any
      const preferred = Number.isFinite(viewing) && data.orgs.some((o) => o.id === viewing) ? viewing : null;
      setOrgId((cur) => (cur !== null && data.orgs.some((o) => o.id === cur) ? cur : (me.tenant_id ?? preferred ?? data.orgs[0]?.id ?? null)));
    } catch { /* leave the list empty; the screens explain it */ }
  }, [me.tenant_id]);
  useEffect(() => { void reload(); }, [reload]);
  return { orgs, orgId, setOrgId, reload, org: orgs.find((o) => o.id === orgId) || null };
}

export function OrgPicker({ me, scope }: { me: Me; scope: ReturnType<typeof useOrgScope> }) {
  if (!me.is_super_admin) return null;                         // a company's own people are fixed to their company
  return (
    <div className="flex items-center gap-3 mb-4">
      <label htmlFor="org-picker" className="text-[12px] font-medium text-stone-600 dark:text-stone-300">Organization</label>
      <select id="org-picker" className={`${inputCls} max-w-xs`} value={scope.orgId ?? ''}
        onChange={(e) => scope.setOrgId(e.target.value ? Number(e.target.value) : null)}>
        {scope.orgs.length === 0 && <option value="">No organizations yet</option>}
        {scope.orgs.map((o) => <option key={o.id} value={o.id}>{o.name}{o.status !== 'active' ? ` (${o.status})` : ''}</option>)}
      </select>
    </div>
  );
}

export function ago(iso: string | null): string {
  if (!iso) return 'never';
  const secs = Math.max(0, (Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).getTime()) / 1000);
  if (secs < 90) return 'just now';
  if (secs < 5400) return `${Math.round(secs / 60)} min ago`;
  if (secs < 129600) return `${Math.round(secs / 3600)} h ago`;
  return `${Math.round(secs / 86400)} d ago`;
}
