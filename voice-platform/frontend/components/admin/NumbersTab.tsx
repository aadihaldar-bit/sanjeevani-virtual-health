'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { History, PhoneIncoming } from 'lucide-react';
import { api, type AgentLite, type Me, type NumberEvent, type PhoneNumber } from '@/lib/adminApi';
import { Badge, Btn, Card, Field, Notice, OrgPicker, Table, ago, inputCls, useAction, useOrgScope } from './ui';

export function NumbersTab({ me, scope }: { me: Me; scope: ReturnType<typeof useOrgScope> }) {
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [filter, setFilter] = useState<'org' | 'unallocated' | 'all'>('org');
  const [history, setHistory] = useState<{ number: string; events: NumberEvent[] } | null>(null);
  const [form, setForm] = useState({ number: '', carrier: 'smartflo', direction: 'both', label: '' });
  const [csv, setCsv] = useState('');
  const [importResult, setImportResult] = useState<{ accepted: string[]; rejected: { row: number; value: string; reason: string }[] } | null>(null);
  const action = useAction();
  const { orgId } = scope;
  const admin = me.capabilities.allocate_numbers;

  const load = useCallback(async () => {
    const query = me.is_super_admin
      ? (filter === 'unallocated' ? '?unallocated=true' : filter === 'all' || orgId === null ? '' : `?tenant_id=${orgId}`)
      : '';
    try { setNumbers((await api<{ numbers: PhoneNumber[] }>(`/api/numbers${query}`)).numbers); } catch { setNumbers([]); }
    try {
      const q = me.is_super_admin && orgId !== null ? `?tenant_id=${orgId}` : '';
      setAgents((await api<{ workflows: AgentLite[] }>(`/api/tenant-workflows${q}`)).workflows);
    } catch { setAgents([]); }
  }, [filter, orgId, me.is_super_admin]);
  useEffect(() => { void load(); }, [load]);

  const orgName = (id: number | null) => (id === null ? 'Unallocated' : scope.orgs.find((o) => o.id === id)?.name || `#${id}`);
  const agentName = (id: number | null) => (id === null ? '—' : agents.find((a) => a.id === id)?.name || `#${id}`);

  const add = async () => {
    const created = await action.run(() => api('/api/numbers', { method: 'POST', body: { ...form, tenant_id: filter === 'org' ? orgId : null } }), 'Number added.');
    if (created) { setForm({ ...form, number: '', label: '' }); await load(); }
  };
  const doImport = async () => {
    const res = await action.run(() => api<{ accepted: string[]; rejected: { row: number; value: string; reason: string }[] }>('/api/numbers/import', { method: 'POST', body: { csv } }));
    if (res) { setImportResult(res); setCsv(''); await load(); }
  };
  const allocate = async (n: PhoneNumber, target: string) => {
    if (!target) return;
    await action.run(() => api(`/api/numbers/${n.id}/allocate`, { method: 'POST', body: { tenant_id: Number(target) } }));
    await load();
  };
  const release = async (n: PhoneNumber) => {
    if (!window.confirm(`Release ${n.number}? Calls to it will stop reaching ${orgName(n.tenant_id)}.`)) return;
    await action.run(() => api(`/api/numbers/${n.id}/release`, { method: 'POST' }));
    await load();
  };
  const assign = async (n: PhoneNumber, target: string) => {
    await action.run(() => api(`/api/numbers/${n.id}/assign`, { method: 'POST', body: { workflow_id: target ? Number(target) : null } }));
    await load();
  };
  const showHistory = async (n: PhoneNumber) => {
    const data = await action.run(() => api<{ number: string; events: NumberEvent[] }>(`/api/numbers/${n.id}/history`));
    if (data) setHistory(data);
  };

  return (
    <div>
      <Card title="Phone numbers" subtitle={admin
        ? 'Add numbers, allocate each to an organization, then the organization assigns it to one of its agents. Calls to a number reach that agent.'
        : 'Numbers allocated to your organization. Assign each to the agent that should answer it.'}
        actions={me.is_super_admin ? (
          <select aria-label="Which numbers" className={`${inputCls} w-44`} value={filter} onChange={(e) => setFilter(e.target.value as 'org' | 'unallocated' | 'all')}>
            <option value="org">This organization</option><option value="unallocated">Unallocated</option><option value="all">All numbers</option>
          </select>) : undefined}>
        {me.is_super_admin && filter === 'org' && <OrgPicker me={me} scope={scope} />}
        {action.error && <Notice kind="error" onClose={action.clear}>{action.error}</Notice>}
        {action.done && <Notice kind="success" onClose={action.clear}>{action.done}</Notice>}
        <Table head={['Number', 'Carrier', 'Direction', 'Tenant', 'Answered by', 'Status', '']} empty="No numbers here. Platform administrators add numbers to the inventory.">
          {numbers.map((n) => (
            <tr key={n.id}>
              <td className="py-2 pr-3 font-mono font-semibold whitespace-nowrap"><PhoneIncoming className="inline w-3 h-3 mr-1 text-stone-400" />{n.number}{n.label && <span className="ml-2 font-sans font-normal text-stone-400">{n.label}</span>}</td>
              <td className="py-2 pr-3 capitalize">{n.carrier}</td>
              <td className="py-2 pr-3">{n.direction}</td>
              <td className="py-2 pr-3">
                {admin ? (
                  <select aria-label={`Tenant of ${n.number}`} className={`${inputCls} w-40`} value={n.tenant_id ?? ''} onChange={(e) => allocate(n, e.target.value)}>
                    <option value="" disabled>Unallocated</option>
                    {scope.orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                  </select>
                ) : orgName(n.tenant_id)}
              </td>
              <td className="py-2 pr-3">
                {n.tenant_id === null ? <span className="text-stone-400">Allocate first</span> : (
                  <select aria-label={`Agent for ${n.number}`} className={`${inputCls} w-44`} value={n.workflow_id ?? ''}
                    onChange={(e) => assign(n, e.target.value)} disabled={!me.capabilities.manage_numbers}>
                    <option value="">Not assigned (default agent)</option>
                    {agents.filter((a) => a.tenant_id === null || a.tenant_id === n.tenant_id).map((a) => <option key={a.id} value={a.id}>{a.name}{a.tenant_id === null ? ' (shared)' : ''}</option>)}
                  </select>
                )}
                {n.workflow_id !== null && !agents.some((a) => a.id === n.workflow_id) && <span className="ml-1 text-[10px] text-stone-400">{agentName(n.workflow_id)}</span>}
              </td>
              <td className="py-2 pr-3"><Badge tone={n.status === 'assigned' ? 'green' : n.status === 'allocated' ? 'blue' : 'grey'}>{n.status}</Badge></td>
              <td className="py-2 text-right whitespace-nowrap">
                <button aria-label={`History of ${n.number}`} onClick={() => showHistory(n)} className="text-stone-400 hover:text-stone-700 dark:hover:text-white mr-2 cursor-pointer"><History className="w-3.5 h-3.5" /></button>
                {admin && n.tenant_id !== null && <Btn kind="ghost" onClick={() => release(n)}>Release</Btn>}
              </td>
            </tr>
          ))}
        </Table>
        {history && (
          <div className="mt-4 rounded-lg border border-stone-200 dark:border-white/10 p-3">
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-semibold">History of {history.number}</p>
              <button onClick={() => setHistory(null)} className="text-[11px] text-stone-400 hover:underline cursor-pointer">Close</button>
            </div>
            <ul className="space-y-1 text-[11px] text-stone-600 dark:text-stone-300">
              {history.events.map((e) => (
                <li key={e.id}><span className="font-semibold capitalize">{e.action}</span>{e.to_tenant_id ? ` to ${orgName(e.to_tenant_id)}` : ''}{e.from_tenant_id && e.action === 'released' ? ` from ${orgName(e.from_tenant_id)}` : ''} · {e.actor} · {ago(e.created_at)}</li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      {admin && (
        <Card title="Add numbers" subtitle="Use international format, for example +918069879488. A 10-digit Indian number is accepted too.">
          <div className="grid grid-cols-1 sm:grid-cols-5 gap-2 items-end max-w-4xl">
            <div className="sm:col-span-2"><Field label="Number"><input className={inputCls} value={form.number} onChange={(e) => setForm({ ...form, number: e.target.value })} placeholder="+918069879488" /></Field></div>
            <Field label="Carrier"><select className={inputCls} value={form.carrier} onChange={(e) => setForm({ ...form, carrier: e.target.value })}><option value="smartflo">Tata Smartflo</option><option value="twilio">Twilio</option><option value="other">Other</option></select></Field>
            <Field label="Direction"><select className={inputCls} value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}><option value="both">Both</option><option value="inbound">Inbound</option><option value="outbound">Outbound</option></select></Field>
            <Btn onClick={add} busy={action.busy} disabled={form.number.trim().length < 8}>Add</Btn>
          </div>
          <div className="mt-5 max-w-4xl">
            <Field label="Or import a CSV" hint="Columns: number, carrier, direction, label (header row optional). Rejected rows are listed with the reason.">
              <textarea className={`${inputCls} font-mono h-24`} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={'+918069879488,smartflo,both,Main line\n+14155550123,twilio,outbound,US out'} />
            </Field>
            <div className="mt-2"><Btn kind="ghost" onClick={doImport} busy={action.busy} disabled={!csv.trim()}>Import</Btn></div>
            {importResult && (
              <div className="mt-3 text-[11px]">
                <p className="text-emerald-700 dark:text-emerald-300 font-semibold">{importResult.accepted.length} added</p>
                {importResult.rejected.length > 0 && (
                  <ul className="mt-1 text-rose-700 dark:text-rose-300">
                    {importResult.rejected.map((r) => <li key={r.row}>Row {r.row} ({r.value || 'empty'}): {r.reason}</li>)}
                  </ul>
                )}
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
