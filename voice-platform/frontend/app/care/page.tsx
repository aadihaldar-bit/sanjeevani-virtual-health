'use client';

/**
 * A one-time link given during a call: upload the prescription and an ID photo, or confirm a payment.
 * Public page (no dashboard sign-in). The link is a long random token and the person also types the short code the agent read out.
 * The payment step is a DEMO: it records the order as paid and takes no money.
 */

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, Loader2, ShieldCheck, Upload, CreditCard, AlertTriangle, FileImage, FileText } from 'lucide-react';
import { getApiBase } from '@/lib/api';

type Info = {
  kind: 'prescription' | 'payment'; status: string; expires_in: number;
  order: { reference: string; amount: number; currency: string; medicines: number } | null;
};

const INPUT = 'w-full h-12 px-4 rounded-xl border border-stone-300 bg-white text-stone-900 text-lg tracking-[0.4em] text-center focus:outline-none focus:ring-2 focus:ring-indigo-500';
const BUTTON = 'w-full h-12 rounded-xl bg-indigo-600 text-white font-semibold flex items-center justify-center gap-2 disabled:opacity-50 hover:bg-indigo-500';

/** Phone photos are 4-12 MB; the page needs legibility, not that much. */
async function downscale(file: File, maxDim = 2400): Promise<Blob> {
  if (!file.type.startsWith('image/')) return file;
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' } as any);
    const scale = Math.min(1, maxDim / Math.max(bmp.width, bmp.height));
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * scale);
    c.height = Math.round(bmp.height * scale);
    c.getContext('2d')!.drawImage(bmp, 0, 0, c.width, c.height);
    return await new Promise<Blob>((ok, bad) => c.toBlob((b) => (b ? ok(b) : bad(new Error('blob'))), 'image/jpeg', 0.9));
  } catch {
    return file;
  }
}

export default function CarePage() {
  const [token, setToken] = useState('');
  const [info, setInfo] = useState<Info | null>(null);
  const [loading, setLoading] = useState(true);
  const [code, setCode] = useState('');
  const [rx, setRx] = useState<File | null>(null);
  const [idDoc, setIdDoc] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(async (t: string) => {
    try {
      const res = await fetch(`${getApiBase()}/api/care/link?t=${encodeURIComponent(t)}`);
      if (!res.ok) { setInfo(null); return; }
      setInfo(await res.json());
    } catch { setInfo(null); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('t') || '';
    setToken(t);
    if (!t) { setLoading(false); return; }
    load(t);
  }, [load]);

  const submitPrescription = async () => {
    if (!rx) { setError('Choose the prescription first.'); return; }
    setBusy(true); setError('');
    try {
      const form = new FormData();
      form.append('t', token);
      form.append('code', code.trim());
      form.append('prescription', await downscale(rx), rx.name || 'prescription');
      if (idDoc) form.append('id_document', await downscale(idDoc), 'id.jpg');
      const res = await fetch(`${getApiBase()}/api/care/prescription`, { method: 'POST', body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setError(data.detail || 'That did not work. Please try again.'); return; }
      setDone(`We have read ${data.medicines} medicine${data.medicines === 1 ? '' : 's'} from your prescription${data.id_received ? ' and received your ID' : ''}. You can go back to the call: the agent will carry on.`);
    } catch { setError('Could not reach the server. Check your connection and try again.'); } finally { setBusy(false); }
  };

  const pay = async () => {
    setBusy(true); setError('');
    try {
      const res = await fetch(`${getApiBase()}/api/care/pay`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ t: token, code: code.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setError(data.detail || 'That did not work. Please try again.'); return; }
      setDone('Payment confirmed. You can go back to the call: the agent will schedule your delivery.');
    } catch { setError('Could not reach the server. Check your connection and try again.'); } finally { setBusy(false); }
  };

  const usable = info && info.status === 'pending';

  return (
    <main className="min-h-screen bg-stone-50 flex items-start justify-center px-4 py-8">
      <div className="w-full max-w-md bg-white rounded-2xl border border-stone-200 shadow-sm p-6 space-y-5">
        <header className="flex items-center gap-2.5">
          <ShieldCheck className="w-6 h-6 text-indigo-600" strokeWidth={1.75} />
          <div>
            <h1 className="text-base font-semibold text-stone-900">Sanjeevani Holdings</h1>
            <p className="text-xs text-stone-500">Secure link from your phone call</p>
          </div>
        </header>

        {loading && <div className="py-10 flex justify-center"><Loader2 className="w-6 h-6 animate-spin text-stone-400" /></div>}

        {!loading && !info && (
          <p className="flex items-start gap-2 text-sm text-stone-700"><AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0" />
            This link is not valid. Ask the agent on the call for a new one.</p>
        )}

        {!loading && info && !usable && !done && (
          <p className="flex items-start gap-2 text-sm text-stone-700"><AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0" />
            {info.status === 'done' ? 'This link has already been used.' : 'This link has expired or is locked. Ask the agent on the call for a new one.'}</p>
        )}

        {done && (
          <div className="flex items-start gap-2 text-sm text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-xl p-3">
            <CheckCircle2 className="w-5 h-5 flex-shrink-0" /> <span>{done}</span>
          </div>
        )}

        {usable && !done && (
          <>
            <div>
              <label htmlFor="care-code" className="block text-sm font-medium text-stone-800 mb-1.5">The six-digit code the agent read out</label>
              <input id="care-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} className={INPUT} placeholder="······" />
            </div>

            {info.kind === 'prescription' && (
              <>
                <div>
                  <span className="block text-sm font-medium text-stone-800 mb-1.5">The prescription (PDF, or a photo of the whole page)</span>
                  <label className="flex items-center gap-2 h-12 px-3 rounded-xl border border-dashed border-stone-300 text-sm text-stone-700 cursor-pointer hover:bg-stone-50">
                    <FileImage className="w-5 h-5 text-stone-400" /> <span className="truncate">{rx ? rx.name : 'Take a photo or choose a file'}</span>
                    <input type="file" accept="image/*,application/pdf" capture="environment" className="sr-only" onChange={(e) => setRx(e.target.files?.[0] || null)} />
                  </label>
                </div>
                <div>
                  <span className="block text-sm font-medium text-stone-800 mb-1.5">Photo of your Emirates ID or passport</span>
                  <label className="flex items-center gap-2 h-12 px-3 rounded-xl border border-dashed border-stone-300 text-sm text-stone-700 cursor-pointer hover:bg-stone-50">
                    <FileText className="w-5 h-5 text-stone-400" /> <span className="truncate">{idDoc ? idDoc.name : 'Take a photo or choose a file'}</span>
                    <input type="file" accept="image/*,application/pdf" capture="environment" className="sr-only" onChange={(e) => setIdDoc(e.target.files?.[0] || null)} />
                  </label>
                </div>
                <button className={BUTTON} disabled={busy || code.length !== 6 || !rx} onClick={submitPrescription}>
                  {busy ? <Loader2 className="w-5 h-5 animate-spin" /> : <Upload className="w-5 h-5" />} Send
                </button>
              </>
            )}

            {info.kind === 'payment' && info.order && (
              <>
                <div className="rounded-xl bg-stone-50 border border-stone-200 p-4 text-center">
                  <p className="text-xs text-stone-500">Order {info.order.reference}</p>
                  <p className="text-3xl font-semibold text-stone-900 mt-1">{info.order.amount} <span className="text-lg">{info.order.currency}</span></p>
                  <p className="text-xs text-stone-500 mt-1">{info.order.medicines} medicine{info.order.medicines === 1 ? '' : 's'}, with delivery</p>
                </div>
                <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-2.5">
                  Demo payment: confirming here marks the order as paid. No card is charged.
                </p>
                <button className={BUTTON} disabled={busy || code.length !== 6} onClick={pay}>
                  {busy ? <Loader2 className="w-5 h-5 animate-spin" /> : <CreditCard className="w-5 h-5" />} Confirm payment
                </button>
              </>
            )}

            {error && <p role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-2.5">{error}</p>}
          </>
        )}
      </div>
    </main>
  );
}
