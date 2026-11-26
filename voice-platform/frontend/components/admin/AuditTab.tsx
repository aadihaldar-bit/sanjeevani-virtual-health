'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { api, type AuditEntry, type Me } from '@/lib/adminApi';
import { Card, OrgPicker, Table, ago, useOrgScope } from './ui';

export function AuditTab({ me, scope }: { me: Me; scope: ReturnType<typeof useOrgScope> }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [allOrgs, setAllOrgs] = useState(false);
  const { orgId } = scope;

  const load = useCallback(async () => {
    const q = me.is_super_admin ? (allOrgs || orgId === null ? '' : `?tenant_id=${orgId}`) : '';
    try { setEntries((await api<{ entries: AuditEntry[] }>(`/api/admin/audit${q}`)).entries); } catch { setEntries([]); }
  }, [allOrgs, orgId, me.is_super_admin]);
  useEffect(() => { void load(); }, [load]);

  const orgName = (id: number | null) => (id === null ? 'Platform' : scope.orgs.find((o) => o.id === id)?.name || `#${id}`);

  return (
    <Card title="Audit log" subtitle="Who changed what, newest first. Secret values are never recorded."
      actions={me.is_super_admin ? <label className="flex items-center gap-1.5 text-[11px]"><input type="checkbox" checked={allOrgs} onChange={(e) => setAllOrgs(e.target.checked)} /> All organizations</label> : undefined}>
      {me.is_super_admin && !allOrgs && <OrgPicker me={me} scope={scope} />}
      <Table head={['When', 'Who', 'Tenant', 'Action', 'Target']} empty="No administrative actions recorded yet.">
        {entries.map((e) => (
          <tr key={e.id}>
            <td className="py-2 pr-3 whitespace-nowrap text-stone-500">{ago(e.created_at)}</td>
            <td className="py-2 pr-3">{e.actor}</td>
            <td className="py-2 pr-3">{orgName(e.tenant_id)}</td>
            <td className="py-2 pr-3 font-mono text-[11px]">{e.action}</td>
            <td className="py-2 pr-3 break-all">{e.target || '—'}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}
