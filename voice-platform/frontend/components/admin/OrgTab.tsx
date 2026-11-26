'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Trash2, UserPlus } from 'lucide-react';
import { api, ApiError, type Me, type Member, type MemberRole, type Org } from '@/lib/adminApi';
import { Badge, Btn, Card, Field, Notice, OrgPicker, Table, inputCls, useAction, useOrgScope } from './ui';

export function OrgTab({ me, scope }: { me: Me; scope: ReturnType<typeof useOrgScope> }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<MemberRole>('operator');
  const orgAction = useAction();
  const memberAction = useAction();
  const handoverAction = useAction();
  const [handover, setHandover] = useState('');
  const timezoneAction = useAction();
  const [zones, setZones] = useState<{ id: string; label: string }[]>([]);
  const [zone, setZone] = useState('');
  const hoursAction = useAction();
  const [hours, setHours] = useState({ start: '09:00', end: '20:00' });
  const { orgId, reload, org } = scope;
  useEffect(() => { setHandover(org?.handover_number || ''); }, [org?.id, org?.handover_number]);
  useEffect(() => { setZone(org?.timezone || ''); }, [org?.id, org?.timezone]);
  useEffect(() => { setHours(org?.calling_window || { start: '09:00', end: '20:00' }); }, [org?.id, org?.calling_window]);
  useEffect(() => { api<{ timezones: { id: string; label: string }[] }>('/api/timezones').then((r) => setZones(r.timezones)).catch(() => setZones([])); }, []);

  const saveHandover = async () => {
    if (orgId === null) return;
    const ok = await handoverAction.run(() => api(`/api/tenants/${orgId}/handover`, { method: 'PUT', body: { number: handover.trim() || null } }),
      handover.trim() ? 'Saved. Callers who ask for a person will be put through to this number.' : 'Removed. The agent will take a message instead of transferring.');
    if (ok !== undefined) await reload();
  };

  const loadMembers = useCallback(async () => {
    if (orgId === null) { setMembers([]); return; }
    try { setMembers((await api<{ members: Member[] }>(`/api/tenants/${orgId}/members`)).members); } catch { setMembers([]); }
  }, [orgId]);
  useEffect(() => { void loadMembers(); }, [loadMembers]);

  const createOrg = async () => {
    const created = await orgAction.run(() => api<{ id: number }>('/api/tenants', { method: 'POST', body: { name } }), 'Tenant created.');
    if (created) { setName(''); await reload(); scope.setOrgId(created.id); }
  };
  const toggleStatus = async (id: number, status: string) => {
    await orgAction.run(() => api(`/api/tenants/${id}`, { method: 'PATCH', body: { status: status === 'active' ? 'suspended' : 'active' } }));
    await reload();
  };
  const invite = async () => {
    const ok = await memberAction.run(() => api(`/api/tenants/${orgId}/members`, { method: 'POST', body: { email, role } }), 'Invitation saved. They can sign in with that email.');
    if (ok) { setEmail(''); await loadMembers(); await reload(); }
  };
  const changeRole = async (m: Member, next: MemberRole) => {
    await memberAction.run(() => api(`/api/tenants/${orgId}/members/${m.id}`, { method: 'PATCH', body: { role: next } }));
    await loadMembers();
  };
  const remove = async (m: Member) => {
    if (!window.confirm(`Remove ${m.email} from the organization?`)) return;
    await memberAction.run(() => api(`/api/tenants/${orgId}/members/${m.id}`, { method: 'DELETE' }));
    await loadMembers(); await reload();
  };

  const createBucket = async (o: Org) => {
    await orgAction.run(async () => {
      const r = await api<{ storage: { status: string; error?: string | null } }>(`/api/tenants/${o.id}/bucket`, { method: 'POST' });
      if (r.storage.status === 'failed') throw new ApiError(r.storage.error || 'Bucket creation failed.', 502);
      return r;
    }, 'Bucket created.');
    await reload();
  };
  const storageCell = (o: Org) => {
    if (o.is_system) return <span className="text-stone-400">shared bucket</span>;
    const st = o.storage;
    if (st?.status === 'ready') return <span title={st.bucket}><Badge tone="green">own bucket</Badge> <span className="font-mono text-stone-500">{st.bucket}</span></span>;
    if (st?.status === 'creating') return <Badge tone="blue">creating…</Badge>;
    if (st?.status === 'failed') return <span title={st.error || ''}><Badge tone="red">failed</Badge> <button className="underline cursor-pointer" onClick={() => createBucket(o)}>Retry</button></span>;
    return <span><Badge>shared bucket</Badge> <button className="underline cursor-pointer" onClick={() => createBucket(o)}>Create own</button></span>;
  };

  return (
    <div>
      {me.is_super_admin && (
        <Card title="Organizations" subtitle="Every customer is an organization. Suspending one blocks its administrators and its phone numbers.">
          {orgAction.error && <Notice kind="error" onClose={orgAction.clear}>{orgAction.error}</Notice>}
          {orgAction.done && <Notice kind="success" onClose={orgAction.clear}>{orgAction.done}</Notice>}
          <Table head={['Name', 'Status', 'Plan', 'Members', 'Storage', '']} empty="No organizations yet. Create the first one below.">
            {scope.orgs.map((o) => (
              <tr key={o.id} className={o.id === scope.orgId ? 'bg-stone-50 dark:bg-white/5' : ''}>
                <td className="py-2 pr-3 font-medium"><button className="hover:underline cursor-pointer" onClick={() => scope.setOrgId(o.id)}>{o.name}</button></td>
                <td className="py-2 pr-3"><Badge tone={o.status === 'active' ? 'green' : 'amber'}>{o.status}</Badge></td>
                <td className="py-2 pr-3">{o.plan || 'standard'}</td>
                <td className="py-2 pr-3">{o.member_count ?? 0}</td>
                <td className="py-2 pr-3 text-[11px]">{storageCell(o)}</td>
                <td className="py-2 text-right"><Btn kind="ghost" onClick={() => toggleStatus(o.id, o.status)}>{o.status === 'active' ? 'Suspend' : 'Reactivate'}</Btn></td>
              </tr>
            ))}
          </Table>
          <div className="flex items-end gap-2 mt-4 max-w-md">
            <div className="flex-1"><Field label="New organization"><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Corp" maxLength={120} /></Field></div>
            <Btn onClick={createOrg} busy={orgAction.busy} disabled={name.trim().length < 2}>Create</Btn>
          </div>
        </Card>
      )}

      {orgId !== null && !org?.is_system && (
        <Card title="Call handover" subtitle="When a caller asks for a person, the agent puts them through to this number. Leave it empty and the agent takes a message instead.">
          {handoverAction.error && <Notice kind="error" onClose={handoverAction.clear}>{handoverAction.error}</Notice>}
          {handoverAction.done && <Notice kind="success" onClose={handoverAction.clear}>{handoverAction.done}</Notice>}
          <div className="flex items-end gap-2 max-w-md">
            <div className="flex-1"><Field label="Handover number" hint="With the country code, for example +919876543210."><input className={inputCls} value={handover} onChange={(e) => setHandover(e.target.value)} placeholder="+91 98765 43210" inputMode="tel" /></Field></div>
            <Btn onClick={saveHandover} busy={handoverAction.busy} disabled={handover.trim() === (org?.handover_number || '')}>Save</Btn>
          </div>
        </Card>
      )}

      {orgId !== null && !org?.is_system && zones.length > 0 && (
        <Card title="Time zone" subtitle="Follow-up calls only go out between 9 AM and 8 PM in this time zone, and the console shows times in it.">
          {timezoneAction.error && <Notice kind="error" onClose={timezoneAction.clear}>{timezoneAction.error}</Notice>}
          {timezoneAction.done && <Notice kind="success" onClose={timezoneAction.clear}>{timezoneAction.done}</Notice>}
          <div className="flex items-end gap-2 max-w-md">
            <div className="flex-1">
              <Field label="Company time zone">
                <select className={inputCls} value={zone || 'Asia/Kolkata'} onChange={(e) => setZone(e.target.value)}>
                  {zones.map((z) => <option key={z.id} value={z.id}>{z.label}</option>)}
                </select>
              </Field>
            </div>
            <Btn busy={timezoneAction.busy} disabled={!zone || zone === (org?.timezone || 'Asia/Kolkata')}
              onClick={async () => { const ok = await timezoneAction.run(() => api(`/api/tenants/${orgId}/timezone`, { method: 'PUT', body: { timezone: zone } }), 'Saved.'); if (ok !== undefined) await reload(); }}>Save</Btn>
          </div>
        </Card>
      )}

      {orgId !== null && !org?.is_system && (
        <Card title="Calling hours" subtitle="Follow-up calls to patients only go out between these times, in the company time zone. A call that comes due outside them waits for the start.">
          {hoursAction.error && <Notice kind="error" onClose={hoursAction.clear}>{hoursAction.error}</Notice>}
          {hoursAction.done && <Notice kind="success" onClose={hoursAction.clear}>{hoursAction.done}</Notice>}
          <div className="flex items-end gap-2 max-w-md">
            <div className="flex-1"><Field label="From"><input type="time" className={inputCls} value={hours.start} onChange={(e) => setHours((h) => ({ ...h, start: e.target.value }))} /></Field></div>
            <div className="flex-1"><Field label="Until"><input type="time" className={inputCls} value={hours.end} onChange={(e) => setHours((h) => ({ ...h, end: e.target.value }))} /></Field></div>
            <Btn busy={hoursAction.busy} disabled={!hours.start || !hours.end || (hours.start === (org?.calling_window?.start || '09:00') && hours.end === (org?.calling_window?.end || '20:00'))}
              onClick={async () => { const ok = await hoursAction.run(() => api(`/api/tenants/${orgId}/calling-hours`, { method: 'PUT', body: hours }), 'Saved. It applies to calls scheduled from now on.'); if (ok !== undefined) await reload(); }}>Save</Btn>
          </div>
        </Card>
      )}

      <Card title="Members" subtitle="Tenant administrators manage members, numbers, models and knowledge for their own organization only.">
        <OrgPicker me={me} scope={scope} />
        {memberAction.error && <Notice kind="error" onClose={memberAction.clear}>{memberAction.error}</Notice>}
        {memberAction.done && <Notice kind="success" onClose={memberAction.clear}>{memberAction.done}</Notice>}
        {orgId === null ? <p className="text-xs text-stone-400">Choose or create an organization first.</p> : (
          <>
            <Table head={['Email', 'Role', 'Status', '']} empty="No members yet.">
              {members.map((m) => (
                <tr key={m.id}>
                  <td className="py-2 pr-3 font-medium">{m.email}</td>
                  <td className="py-2 pr-3">
                    {m.role === 'super_admin' ? <Badge tone="blue">Super administrator</Badge> : (
                    <select aria-label={`Role of ${m.email}`} className={`${inputCls} w-36`} value={m.role} onChange={(e) => changeRole(m, e.target.value as MemberRole)}>
                      <option value="viewer">Viewer</option>
                      <option value="operator">Member</option>
                      <option value="manager">Manager</option>
                      <option value="tenant_admin">Administrator</option>
                    </select>)}
                  </td>
                  <td className="py-2 pr-3"><Badge tone={m.status === 'active' ? 'green' : 'blue'}>{m.status === 'active' ? 'active' : 'invited'}</Badge></td>
                  <td className="py-2 text-right">{m.role === 'super_admin' ? <span className="text-[10px] text-stone-400" title="Super administrators are set in the server settings">set in server settings</span> : <button aria-label={`Remove ${m.email}`} onClick={() => remove(m)} className="text-rose-500 hover:text-rose-700 cursor-pointer"><Trash2 className="w-3.5 h-3.5" /></button>}</td>
                </tr>
              ))}
            </Table>
            <div className="flex flex-wrap items-end gap-2 mt-4">
              <div className="w-64"><Field label="Invite by email"><input className={inputCls} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="person@company.com" /></Field></div>
              <div className="w-40"><Field label="Role">
                <select className={inputCls} value={role} onChange={(e) => setRole(e.target.value as MemberRole)}>
                  <option value="viewer">Viewer</option><option value="operator">Member</option><option value="manager">Manager</option><option value="tenant_admin">Administrator</option>
                </select>
              </Field></div>
              <Btn onClick={invite} busy={memberAction.busy} disabled={!email.includes('@')}><UserPlus className="w-3 h-3" /> Invite</Btn>
            </div>
            <p className="text-[10px] text-stone-400 mt-2">Invited people must sign in with Google using that exact address. An organization always keeps at least one administrator.</p>
          </>
        )}
      </Card>
    </div>
  );
}
