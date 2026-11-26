'use client';

/**
 * Patient identity verification (KYC) — opened on the patient's phone while on the call.
 * code (spoken by the voice agent) → consent → Aadhaar Secure QR → liveness selfie → result.
 * Public page: no dashboard login. Access is bound to a short-lived, single-session token.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ShieldCheck, ScanLine, Camera, CheckCircle2, XCircle, Loader2, Upload, RefreshCw, ArrowRight, AlertTriangle,
} from 'lucide-react';
import { getApiBase } from '@/lib/api';

type Phase = { id: 'straight' | 'swing' | 'extra'; frames: number; seconds: number; prompt: string };
type Identity = { name: string | null; last4: string | null; birth_year?: string | null; demo?: boolean };
type Session = {
  token: string; status: string; expires_in: number; consent_given: boolean; demo_mode: boolean;
  challenge: { extra: string; phases: Phase[] };
  qr_attempts_left: number; selfie_attempts_left: number; identity: Identity | null;
};
type Step = 'code' | 'consent' | 'qr' | 'selfie' | 'done' | 'failed';

async function call(path: string, body?: FormData | object) {
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData;
  const res = await fetch(`${getApiBase()}${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined || isForm ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

/** Phone photos are 4–12 MB; the QR needs resolution but not that much. */
async function downscale(file: File, maxDim = 3000): Promise<Blob> {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' } as any);
    const scale = Math.min(1, maxDim / Math.max(bmp.width, bmp.height));
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * scale);
    c.height = Math.round(bmp.height * scale);
    c.getContext('2d')!.drawImage(bmp, 0, 0, c.width, c.height);
    return await new Promise<Blob>((ok, bad) => c.toBlob((b) => (b ? ok(b) : bad(new Error('blob'))), 'image/jpeg', 0.92));
  } catch {
    return file;
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function KycPage() {
  const [step, setStep] = useState<Step>('code');
  const [code, setCode] = useState('');
  const [session, setSession] = useState<Session | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [agree, setAgree] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [pdfPwd, setPdfPwd] = useState('');
  const [result, setResult] = useState<{ ok: boolean; messages: string[]; final: boolean } | null>(null);

  // selfie capture state
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [camOn, setCamOn] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [countdown, setCountdown] = useState<number | null>(null);
  const [progress, setProgress] = useState(0);
  const [analyzing, setAnalyzing] = useState(false);

  const token = session?.token;

  const route = useCallback((s: Session) => {
    setSession(s);
    if (s.identity) setIdentity(s.identity);
    if (s.status === 'verified') setStep('done');
    else if (s.status === 'failed' || s.status === 'expired') setStep('failed');
    else if (s.status === 'qr_verified') setStep('selfie');
    else if (s.consent_given) setStep('qr');
    else setStep('consent');
  }, []);

  // resume a session after refresh, or prefill ?code=
  useEffect(() => {
    const saved = typeof window !== 'undefined' ? sessionStorage.getItem('kyc_token') : null;
    const qp = new URLSearchParams(window.location.search).get('code');
    if (qp) setCode(qp.replace(/\D/g, '').slice(0, 6));
    if (saved) {
      call(`/api/kyc/s/${saved}`).then(({ ok, data }) => {
        if (ok && !['failed', 'expired', 'verified'].includes(data.status)) route(data as Session);
        else sessionStorage.removeItem('kyc_token');
      });
    }
    return () => stopCamera();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function stopCamera() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setCamOn(false);
  }

  async function submitCode() {
    setError(null);
    if (code.length !== 6) return setError('Enter the 6-digit code the receptionist read out.');
    setBusy(true);
    const { ok, data } = await call('/api/kyc/exchange', { code });
    setBusy(false);
    if (!ok) return setError(data.detail || 'That code is not valid.');
    sessionStorage.setItem('kyc_token', data.token);
    route(data as Session);
  }

  async function giveConsent() {
    if (!token) return;
    setBusy(true);
    const { ok, data } = await call(`/api/kyc/s/${token}/consent`, {});
    setBusy(false);
    if (!ok) return setError(data.detail || 'Could not record consent.');
    route(data as Session);
  }

  async function onQrPicked(file?: File | null) {
    if (!file || !token) return;
    setError(null);
    setNotes([]);
    if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) {
      setPdfFile(file);   // e-Aadhaar PDF: ask for its password, then submit
      setPreview(null);
      return;
    }
    setPdfFile(null);
    setPreview(URL.createObjectURL(file));
    setBusy(true);
    const blob = await downscale(file);
    const fd = new FormData();
    fd.append('image', blob, 'aadhaar.jpg');
    await submitQr(fd);
  }

  async function submitPdf() {
    if (!pdfFile || !token) return;
    setError(null);
    setNotes([]);
    setBusy(true);
    const fd = new FormData();
    fd.append('image', pdfFile, 'aadhaar.pdf');
    fd.append('password', pdfPwd.trim());
    await submitQr(fd);
  }

  async function submitQr(fd: FormData) {
    const { ok, data } = await call(`/api/kyc/s/${token}/aadhaar`, fd);
    setBusy(false);
    setPdfPwd('');
    if (ok && data.ok) {
      setIdentity(data.identity);
      setStep('selfie');
      return;
    }
    if (data.final) { setStep('failed'); return; }
    setError(data.message || data.detail || 'Could not verify that QR.');
    if (typeof data.attempts_left === 'number') setNotes([`${data.attempts_left} attempt${data.attempts_left === 1 ? '' : 's'} left`]);
  }

  async function startSelfie() {
    if (!session || !token) return;
    setError(null);
    setResult(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } }, audio: false,
      });
      streamRef.current = stream;
      setCamOn(true);
      await sleep(80);
      const v = videoRef.current!;
      v.srcObject = stream;
      await v.play();
      for (let n = 3; n >= 1; n--) { setCountdown(n); setPrompt('Get ready — look straight at the camera'); await sleep(900); }
      setCountdown(null);

      const frames: Blob[] = [];
      const phases: string[] = [];
      const canvas = document.createElement('canvas');
      for (const ph of session.challenge.phases) {
        setPrompt(ph.prompt);
        const gap = (ph.seconds * 1000) / ph.frames;
        for (let i = 0; i < ph.frames; i++) {
          setProgress(((i + 1) / ph.frames) * 100);
          await sleep(gap);
          const vw = v.videoWidth || 640, vh = v.videoHeight || 480;
          const w = 480, h = Math.round((vh / vw) * w);
          canvas.width = w; canvas.height = h;
          canvas.getContext('2d')!.drawImage(v, 0, 0, w, h);
          const b = await new Promise<Blob | null>((r) => canvas.toBlob(r, 'image/jpeg', 0.82));
          if (b) { frames.push(b); phases.push(ph.id); }
        }
        setProgress(0);
      }
      stopCamera();
      setPrompt('');
      setAnalyzing(true);

      const fd = new FormData();
      fd.append('meta', JSON.stringify({ phases }));
      frames.forEach((b, i) => fd.append('frames', b, `f${i}.jpg`));
      const { ok, data } = await call(`/api/kyc/s/${token}/selfie`, fd);
      setAnalyzing(false);
      if (ok && data.ok) { setResult({ ok: true, messages: [], final: true }); setStep('done'); return; }
      if (data.final) { setStep('failed'); return; }
      setResult({ ok: false, messages: data.messages || [data.detail || 'Verification did not pass.'], final: false });
      if (typeof data.attempts_left === 'number') setNotes([`${data.attempts_left} attempt${data.attempts_left === 1 ? '' : 's'} left`]);
    } catch (e: any) {
      stopCamera();
      setAnalyzing(false);
      setError(e?.name === 'NotAllowedError'
        ? 'Camera access was blocked. Allow the camera in your browser settings and try again.'
        : 'We could not use the camera. Please try again.');
    }
  }

  const first = (identity?.name || '').split(' ')[0];
  const card = 'w-full max-w-md bg-white rounded-2xl border border-stone-200 shadow-sm p-6';

  return (
    <div className="min-h-screen bg-[#fbfbfa] flex flex-col items-center justify-start sm:justify-center p-4 text-stone-800">
      <div className="w-full max-w-md mb-4 flex items-center gap-2">
        <div className="w-9 h-9 rounded-xl bg-emerald-50 flex items-center justify-center"><ShieldCheck className="w-5 h-5 text-emerald-600" /></div>
        <div>
          <div className="text-sm font-semibold">STL City Care</div>
          <div className="text-[11px] text-stone-500">Secure identity verification</div>
        </div>
      </div>

      {session?.demo_mode && (
        <div className="w-full max-w-md mb-3 text-[11px] bg-amber-50 border border-amber-200 text-amber-800 rounded-xl px-3 py-2 flex gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0" /> DEMO MODE — demo credentials are accepted. Real Aadhaar QRs are checked against UIDAI.
        </div>
      )}

      {/* ── code ── */}
      {step === 'code' && (
        <div className={card}>
          <h1 className="text-lg font-semibold">Enter your verification code</h1>
          <p className="text-sm text-stone-500 mt-1">Type the 6-digit code our receptionist just read out on your call.</p>
          <input inputMode="numeric" pattern="[0-9]*" autoFocus maxLength={6} value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            onKeyDown={(e) => e.key === 'Enter' && submitCode()}
            placeholder="••••••" aria-label="Verification code"
            className="mt-5 w-full text-center tracking-[0.5em] text-2xl font-semibold border border-stone-300 rounded-xl py-3 focus:outline-none focus:ring-2 focus:ring-emerald-500" />
          {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
          <button onClick={submitCode} disabled={busy || code.length !== 6}
            className="mt-5 w-full bg-emerald-600 disabled:bg-stone-300 text-white rounded-xl py-3 font-medium flex items-center justify-center gap-2">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />} Continue
          </button>
        </div>
      )}

      {/* ── consent ── */}
      {step === 'consent' && (
        <div className={card}>
          <h1 className="text-lg font-semibold">Before we begin</h1>
          <p className="text-sm text-stone-600 mt-2">To book your appointment we need to confirm who you are. We will:</p>
          <ul className="text-sm text-stone-600 mt-2 space-y-1 list-disc pl-5">
            <li>read the UIDAI-signed QR on your Aadhaar to confirm your name,</li>
            <li>compare a short live selfie with the photo in that QR,</li>
            <li>check that you are a real, live person.</li>
          </ul>
          <p className="text-sm text-stone-600 mt-3">
            We keep only your <b>name, birth year, last 4 digits of Aadhaar</b> and the result. We do <b>not</b> store your
            full Aadhaar number, photos or video. Used only to verify this appointment. You can withdraw consent by telling the clinic.
          </p>
          <p className="text-xs text-stone-500 mt-2">
            हम केवल आपका नाम, जन्म वर्ष, आधार के अंतिम 4 अंक और परिणाम रखते हैं। पूरा आधार नंबर, फ़ोटो या वीडियो संग्रहित नहीं किया जाता।
          </p>
          <label className="mt-4 flex items-start gap-2 text-sm">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-1 w-4 h-4" />
            <span>I agree to this verification and to the processing described above.</span>
          </label>
          {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
          <button onClick={giveConsent} disabled={!agree || busy}
            className="mt-5 w-full bg-emerald-600 disabled:bg-stone-300 text-white rounded-xl py-3 font-medium flex items-center justify-center gap-2">
            {busy && <Loader2 className="w-4 h-4 animate-spin" />} I agree — continue
          </button>
        </div>
      )}

      {/* ── Aadhaar QR ── */}
      {step === 'qr' && (
        <div className={card}>
          <div className="flex items-center gap-2"><ScanLine className="w-5 h-5 text-emerald-600" /><h1 className="text-lg font-semibold">Step 1 of 2 — Aadhaar QR</h1></div>
          <p className="text-sm text-stone-500 mt-2">
            Take a clear photo of the <b>QR code</b> on your Aadhaar card (or e-Aadhaar). Keep it flat, fill the frame, in good light — avoid glare.
          </p>
          {preview && <img src={preview} alt="" className="mt-4 rounded-xl max-h-48 w-full object-contain bg-stone-50 border" />}
          {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
          {notes.map((n) => <p key={n} className="text-xs text-stone-500 mt-1">{n}</p>)}
          <label className={`mt-5 w-full bg-emerald-600 text-white rounded-xl py-3 font-medium flex items-center justify-center gap-2 cursor-pointer ${busy ? 'opacity-60 pointer-events-none' : ''}`}>
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Camera className="w-4 h-4" />} {busy ? 'Verifying with UIDAI signature…' : 'Open camera & scan QR'}
            <input type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => onQrPicked(e.target.files?.[0])} />
          </label>
          <label className={`mt-2 w-full border border-stone-300 rounded-xl py-2.5 text-sm text-stone-700 flex items-center justify-center gap-2 cursor-pointer ${busy ? 'opacity-60 pointer-events-none' : ''}`}>
            <Upload className="w-4 h-4" /> Upload from gallery instead
            <input type="file" accept="image/*,application/pdf" className="hidden" onChange={(e) => onQrPicked(e.target.files?.[0])} />
          </label>
          <p className="text-xs text-stone-400 mt-2">Have the Aadhaar PDF (DigiLocker or UIDAI)? Choose it above. DigiLocker PDFs have no password; UIDAI e-Aadhaar PDFs use the first 4 letters of your name in CAPITALS + birth year (e.g. RAMA1990).</p>
          {pdfFile && (
            <div className="mt-3 border border-stone-200 rounded-xl p-3">
              <p className="text-sm text-stone-700 truncate">📄 {pdfFile.name}</p>
              <input type="text" autoCapitalize="characters" autoComplete="off" value={pdfPwd} onChange={(e) => setPdfPwd(e.target.value)}
                placeholder="PDF password (leave empty if none, e.g. DigiLocker)" aria-label="PDF password"
                className="mt-2 w-full border border-stone-300 rounded-lg px-3 py-2 text-sm" />
              <button onClick={submitPdf} disabled={busy}
                className="mt-2 w-full bg-emerald-600 text-white rounded-lg py-2.5 text-sm font-medium disabled:opacity-50">Verify PDF</button>
            </div>
          )}
        </div>
      )}

      {/* ── selfie ── */}
      {step === 'selfie' && (
        <div className={card}>
          <div className="flex items-center gap-2"><Camera className="w-5 h-5 text-emerald-600" /><h1 className="text-lg font-semibold">Step 2 of 2 — Face check</h1></div>
          {identity && (
            <div className="mt-3 text-sm bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-2">
              <div className="flex items-center gap-1.5 text-emerald-700 font-medium"><CheckCircle2 className="w-4 h-4" /> Aadhaar verified</div>
              <div className="text-stone-700 mt-0.5">{identity.name} · XXXX XXXX {identity.last4}{identity.demo ? ' · DEMO' : ''}</div>
            </div>
          )}
          {!camOn && !analyzing && (
            <>
              <p className="text-sm text-stone-500 mt-3">
                We'll ask you to look at the camera, turn your head left and right, and make one more small move. Keep your face in the oval, in good light. Only you should be in view.
              </p>
              {result && !result.ok && (
                <div className="mt-3 text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-xl px-3 py-2 space-y-1">
                  {result.messages.map((m) => <div key={m}>• {m}</div>)}
                </div>
              )}
              {notes.map((n) => <p key={n} className="text-xs text-stone-500 mt-1">{n}</p>)}
              {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
              <button onClick={startSelfie}
                className="mt-5 w-full bg-emerald-600 text-white rounded-xl py-3 font-medium flex items-center justify-center gap-2">
                {result && !result.ok ? <RefreshCw className="w-4 h-4" /> : <Camera className="w-4 h-4" />} {result && !result.ok ? 'Try again' : 'Start face check'}
              </button>
            </>
          )}
          {camOn && (
            <div className="mt-4">
              <div className="relative rounded-2xl overflow-hidden bg-black aspect-[3/4]">
                <video ref={videoRef} playsInline muted autoPlay className="w-full h-full object-cover [transform:scaleX(-1)]" />
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                  <div className="w-[62%] h-[70%] rounded-[50%] border-4 border-white/80 shadow-[0_0_0_999px_rgba(0,0,0,0.35)]" />
                </div>
                {countdown !== null && (
                  <div className="absolute inset-0 flex items-center justify-center text-white text-7xl font-bold drop-shadow">{countdown}</div>
                )}
              </div>
              <div className="mt-3 text-center text-base font-medium text-stone-800 min-h-[3rem]">{prompt}</div>
              <div className="h-1.5 bg-stone-200 rounded-full overflow-hidden"><div className="h-full bg-emerald-500 transition-all" style={{ width: `${progress}%` }} /></div>
            </div>
          )}
          {analyzing && (
            <div className="mt-6 flex flex-col items-center gap-3 py-8 text-stone-600">
              <Loader2 className="w-8 h-8 animate-spin text-emerald-600" />
              <div className="text-sm">Checking your face against the Aadhaar photo…</div>
            </div>
          )}
        </div>
      )}

      {/* ── done ── */}
      {step === 'done' && (
        <div className={`${card} text-center`}>
          <CheckCircle2 className="w-14 h-14 text-emerald-500 mx-auto" />
          <h1 className="text-xl font-semibold mt-3">You're verified{first ? `, ${first}` : ''}</h1>
          <p className="text-sm text-stone-600 mt-2">Go back to your call and tell the receptionist <b>"done"</b> — she'll continue with your appointment.</p>
          {identity?.demo && <p className="text-[11px] text-amber-700 mt-3">Verified with a DEMO credential.</p>}
          <p className="text-[11px] text-stone-400 mt-4">You can now close this page.</p>
        </div>
      )}

      {/* ── failed ── */}
      {step === 'failed' && (
        <div className={`${card} text-center`}>
          <XCircle className="w-14 h-14 text-rose-500 mx-auto" />
          <h1 className="text-xl font-semibold mt-3">We couldn't verify you this time</h1>
          <p className="text-sm text-stone-600 mt-2">
            Please go back to your call and tell the receptionist. You can ask for a new code to try again, or visit the clinic with your original Aadhaar card.
          </p>
        </div>
      )}

      <p className="text-[11px] text-stone-400 mt-4 max-w-md text-center">
        Your Aadhaar QR is verified using UIDAI's digital signature. We never store your full Aadhaar number, photos or video.
      </p>
    </div>
  );
}
