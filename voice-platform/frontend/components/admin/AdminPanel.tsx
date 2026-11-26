'use client';

import React from 'react';
import { ShieldCheck } from 'lucide-react';
import { useMe } from '@/lib/useMe';
import type { Me } from '@/lib/adminApi';
import { Badge, useOrgScope } from './ui';
import { Card, EmptyState, Page, Skeleton } from '@/components/ui/kit';
import { OrgTab } from './OrgTab';
import { NumbersTab } from './NumbersTab';
import { ModelsTab } from './ModelsTab';
import { VectorTab } from './VectorTab';
import { AuditTab } from './AuditTab';

type TabKey = 'org' | 'numbers' | 'models' | 'vector' | 'audit';

const TITLES: Record<TabKey, { title: string; subtitle: string }> = {
  org: { title: 'Team & access', subtitle: 'Who can use this organization, and what they may do.' },
  numbers: { title: 'Phone numbers', subtitle: 'Numbers allocated to your organization, and which agent answers each one.' },
  models: { title: 'Models & keys', subtitle: 'Use your own provider keys or your own model server instead of the platform defaults.' },
  vector: { title: 'Vector database', subtitle: 'Point knowledge search at your own Qdrant instead of the platform one.' },
  audit: { title: 'Audit log', subtitle: 'Who changed what, newest first.' },
};

/** One Settings page. Each area (team, numbers, models ...) is its own tab in the Settings bar. */
export function AdminPanel({ section = 'org' }: { section?: TabKey }) {
  const { me, loading } = useMe();
  if (loading) return <Page><Skeleton className="h-40 rounded-xl" /></Page>;
  if (!me || (me.role !== 'tenant_admin' && me.role !== 'super_admin' && !me.capabilities.manage_numbers)) {
    return (
      <Page width="max-w-xl">
        <Card>
          <EmptyState
            icon={<ShieldCheck className="w-5 h-5" strokeWidth={1.75} />}
            title="Settings are for administrators"
            text="Ask an administrator of your organization if you need to change members, numbers, models or knowledge settings."
          />
        </Card>
      </Page>
    );
  }
  return <AdminBody me={me} section={section} />;
}

function AdminBody({ me, section }: { me: Me; section: TabKey }) {
  const scope = useOrgScope(me);
  const suspended = me.tenant_status === 'suspended';
  const { title, subtitle } = TITLES[section];
  return (
    <Page title={title} subtitle={subtitle} actions={suspended ? <Badge tone="red">organization suspended</Badge> : undefined} width="max-w-5xl">
      <div className="space-y-6 [&>div]:space-y-6">
        {section === 'org' && <OrgTab me={me} scope={scope} />}
        {section === 'numbers' && <NumbersTab me={me} scope={scope} />}
        {section === 'models' && <ModelsTab me={me} scope={scope} />}
        {section === 'vector' && <VectorTab me={me} scope={scope} />}
        {section === 'audit' && <AuditTab me={me} scope={scope} />}
      </div>
    </Page>
  );
}
