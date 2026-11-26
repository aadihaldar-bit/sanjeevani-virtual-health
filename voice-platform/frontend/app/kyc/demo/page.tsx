'use client';

/**
 * DEMO credential generator (only works while the server runs with KYC_DEMO_MODE=true).
 * Creates a Secure-QR-format credential for the presenter's OWN photo, signed with a demo key —
 * never a UIDAI key — so the full KYC flow can be shown without a real Aadhaar.
 */

import { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, QrCode } from 'lucide-react';
import { getApiBase } from '@/lib/api';

export default function KycDemoCredential() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [name, setName] = useState('Demo Patient');
  const [dob, setDob] = useState('01-01-1990');
  const [gender, setGender] = useState('M');
  const [photo, setPhoto] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [png, setPng] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${getApiBase()}/api/kyc/config`).then((r) => r.json()).then((d) => setEnabled(!!d.demo_mode)).catch(() => setEnabled(false));
  }, []);

  async function generate() {
    if (!photo) return setErr('Choose a clear, front-facing photo of yourself.');
    setErr(null); setBusy(true); setPng(null);
    const fd = new FormData();
    fd.append('name', name); fd.append('dob', dob); fd.append('gender', gender); fd.append('photo', photo);
    try {
      const res = await fetch(`${getApiBase()}/api/kyc/demo/credential`, { method: 'POST', body: fd });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        setErr(j.reason === 'no_face_in_photo' ? 'No face found in that photo — use a clear, front-facing one.' : (j.detail || 'Could not generate the credential.'));
      } else {
        setPng(URL.createObjectURL(await res.blob()));
      }
    } catch { setErr('Network error.'); }
    setBusy(false);
  }

  if (enabled === false) {
    return <div className="min-h-screen flex items-center justify-center text-stone-500 text-sm p-6">Demo credentials are not available on this server.</div>;
  }

  return (
    <div className="min-h-screen bg-[#fbfbfa] flex flex-col items-center p-4 text-stone-800">
      <div className="w-full max-w-md bg-white rounded-2xl border border-stone-200 p-6 mt-6">
        <div className="flex items-center gap-2"><QrCode className="w-5 h-5 text-emerald-600" /><h1 className="text-lg font-semibold">Demo Aadhaar credential</h1></div>
        <div className="mt-3 text-[11px] bg-amber-50 border border-amber-200 text-amber-800 rounded-xl px-3 py-2 flex gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          Signed with a DEMO key, not UIDAI. Accepted only while the server is in demo mode, and always labelled "DEMO" in the dashboard.
        </div>
        <label className="block text-xs text-stone-500 mt-4">Name (as it should appear)</label>
        <input value={name} onChange={(e) => setName(e.target.value)} className="w-full border border-stone-300 rounded-lg px-3 py-2 text-sm" />
        <div className="grid grid-cols-2 gap-3 mt-3">
          <div><label className="block text-xs text-stone-500">Date of birth</label>
            <input value={dob} onChange={(e) => setDob(e.target.value)} className="w-full border border-stone-300 rounded-lg px-3 py-2 text-sm" /></div>
          <div><label className="block text-xs text-stone-500">Gender</label>
            <select value={gender} onChange={(e) => setGender(e.target.value)} className="w-full border border-stone-300 rounded-lg px-3 py-2 text-sm">
              <option value="M">M</option><option value="F">F</option><option value="T">T</option></select></div>
        </div>
        <label className="block text-xs text-stone-500 mt-3">Your photo (clear, front-facing)</label>
        <input type="file" accept="image/*" capture="user" onChange={(e) => setPhoto(e.target.files?.[0] || null)} className="w-full text-sm" />
        {err && <p className="mt-3 text-sm text-rose-600">{err}</p>}
        <button onClick={generate} disabled={busy} className="mt-5 w-full bg-emerald-600 disabled:bg-stone-300 text-white rounded-xl py-3 font-medium flex items-center justify-center gap-2">
          {busy && <Loader2 className="w-4 h-4 animate-spin" />} Generate QR
        </button>
        {png && (
          <div className="mt-5">
            <img src={png} alt="Demo Aadhaar QR" className="w-full border rounded-lg" />
            <p className="text-xs text-stone-500 mt-2">Show this full-screen at maximum brightness (or print it), then scan it in the verification step with the phone.</p>
            <a href={png} download="demo-aadhaar-qr.png" className="mt-2 inline-block text-sm text-emerald-700 underline">Download PNG</a>
          </div>
        )}
      </div>
    </div>
  );
}
