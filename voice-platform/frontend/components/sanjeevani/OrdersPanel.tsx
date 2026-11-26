'use client';

import React, { useState } from 'react';
import { ArrowRight, Package, RefreshCw } from 'lucide-react';
import { api } from '@/lib/adminApi';
import { useDashboardStore } from '@/lib/store';
import { useWorkspace } from '@/lib/useMe';
import { formatWhen } from '@/lib/format';
import { PatientLabel, useCompanyZone, useListing } from '@/lib/usePatientCare';
import { Badge, Button, Card, EmptyState, Page, Skeleton } from '@/components/ui/kit';

interface OrderItem { drug_name: string | null; strength: string | null; dose_text: string | null; price: number | null; status?: string }
interface Order {
  id: number;
  reference: string | null;
  status: string;
  patient: PatientLabel | null;
  requested_text: string | null;
  items: OrderItem[];
  contact_phone: string | null;
  address: string | null;
  amount: number | null;
  payment_status: string;
  delivery_slot: string | null;
  id_document_received: boolean;
  created_at: string | null;
  call_id: number | null;
}

const STATUS: Record<string, { label: string; tone: 'blue' | 'green' | 'grey' | 'amber' }> = {
  awaiting_prescription: { label: 'Waiting for the prescription', tone: 'amber' },
  prescription_received: { label: 'Prescription received', tone: 'amber' },
  awaiting_payment: { label: 'Waiting for payment', tone: 'amber' },
  paid: { label: 'Paid', tone: 'blue' },
  scheduled: { label: 'Delivery scheduled', tone: 'blue' },
  delivered: { label: 'Delivered', tone: 'green' },
  cancelled: { label: 'Cancelled', tone: 'grey' },
};

/** Medicine orders taken on calls from patients: the prescription they uploaded, the address, the payment and the delivery. */
export function OrdersPanel() {
  const { me } = useWorkspace();
  const zone = useCompanyZone();
  const { selectCall } = useDashboardStore();
  const list = useListing<Order>('/api/sanjeevani/orders', 'orders');
  const isAdmin = !!me && (me.is_super_admin || me.role === 'tenant_admin');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const setStatus = async (o: Order, status: string) => {
    setSaving(true); setError('');
    try {
      await api(`/api/sanjeevani/orders/${o.id}`, { method: 'PUT', body: { status } });
      list.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That could not be saved.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Page title="Medicine orders" subtitle="Orders patients placed by phone. The prescription is read from the photo they sent; an unclear dose is left for the pharmacist."
      actions={<Button onClick={list.reload}><RefreshCw className="w-3.5 h-3.5" /> Refresh</Button>}>
      {error && <p role="alert" className="text-[13px] text-red-700 dark:text-red-300">{error}</p>}
      <Card padded={false} title="Orders">
        {list.rows === null ? (
          <div className="p-5 pt-3 space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : list.rows.length === 0 ? (
          <EmptyState icon={<Package className="w-5 h-5" strokeWidth={1.75} />} title="No orders yet"
            text="When a patient phones in to order medicine, the order appears here as it moves from prescription to delivery." />
        ) : (
          <ul className="mt-3 divide-y divide-stone-100 dark:divide-white/5">
            {list.rows.map((o) => {
              const s = STATUS[o.status] || { label: o.status, tone: 'grey' as const };
              const unclear = o.items.filter((i) => i.status === 'needs_clarification' || !i.dose_text).length;
              return (
                <li key={o.id} className="flex flex-wrap items-start gap-x-4 gap-y-2 px-5 py-3">
                  <div className="min-w-0 flex-1 basis-64">
                    <p className="text-[13px] font-medium text-stone-900 dark:text-white">{o.reference} · {o.patient?.name || 'New patient'}</p>
                    <p className="text-[13px] text-stone-600 dark:text-stone-300">
                      {o.items.length ? o.items.map((i) => [i.drug_name, i.strength].filter(Boolean).join(' ')).join(', ') : (o.requested_text || 'No medicines yet')}
                    </p>
                    <p className="text-[12px] text-stone-500 dark:text-stone-400">
                      {[o.amount != null ? `${o.amount} AED` : '', o.payment_status === 'paid' ? 'paid' : 'unpaid', o.id_document_received ? 'ID received' : 'no ID yet',
                        unclear ? `${unclear} for the pharmacist to confirm` : '', o.delivery_slot ? `delivery ${o.delivery_slot}` : '', o.address || '',
                        formatWhen(o.created_at, zone) && `started ${formatWhen(o.created_at, zone)}`].filter(Boolean).join(' · ')}
                    </p>
                  </div>
                  <Badge tone={s.tone}>{s.label}</Badge>
                  <div className="flex items-center gap-1.5">
                    {o.call_id && (
                      <button onClick={() => selectCall(o.call_id)} className="inline-flex items-center gap-1 mr-1 text-[12px] text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer">
                        Open call <ArrowRight className="w-3 h-3" />
                      </button>
                    )}
                    {isAdmin && o.status === 'scheduled' && <Button className="!h-7 !px-2.5 text-[12px]" disabled={saving} onClick={() => setStatus(o, 'delivered')}>Mark delivered</Button>}
                    {isAdmin && !['delivered', 'cancelled'].includes(o.status) && <Button variant="ghost" className="!h-7 !px-2.5 text-[12px]" disabled={saving} onClick={() => setStatus(o, 'cancelled')}>Cancel</Button>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </Page>
  );
}
