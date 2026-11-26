'use client';

/**
 * InitiateAICallPanel
 * UI panel for initiating custom outbound AI calls from the dashboard.
 * Handles: idle → loading → success | error states.
 * Integrates with the existing apiFetch pattern via initiateOutboundCall().
 */

import React, { useState, useRef } from 'react';
import {
  PhoneCall, Loader2, CheckCircle2, XCircle, X, Zap,
} from 'lucide-react';
import { initiateOutboundCall } from '@/lib/api';

// ─── Types ─────────────────────────────────────────────────────

type CallState = 'idle' | 'calling' | 'success' | 'error';

// ─── Component ─────────────────────────────────────────────────

export function InitiateAICallPanel() {
  const [phoneNumber, setPhoneNumber] = useState('');
  const [callState, setCallState] = useState<CallState>('idle');
  const [result, setResult] = useState<{ status: string; call_sid: string } | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Auto-reset timer ref so we can cancel on unmount
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Basic E.164 client-side check before hitting the backend. */
  function isValidE164(num: string): boolean {
    return /^\+[1-9]\d{7,14}$/.test(num.trim());
  }

  async function handleInitiateCall() {
    const trimmed = phoneNumber.trim();
    if (!trimmed) return;

    if (!isValidE164(trimmed)) {
      setErrorMsg(
        `"${trimmed}" is not a valid E.164 number. Use format: +14155552671`
      );
      setCallState('error');
      return;
    }

    setCallState('calling');
    setResult(null);
    setErrorMsg(null);

    try {
      const data = await initiateOutboundCall(trimmed);
      setResult(data);
      setCallState('success');
      setPhoneNumber('');

      // Auto-reset to idle after 10 s
      if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
      resetTimerRef.current = setTimeout(() => {
        setCallState('idle');
        setResult(null);
      }, 10_000);
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : 'Failed to initiate call. Check backend logs.';
      setErrorMsg(msg);
      setCallState('error');
    }
  }

  function handleDismissError() {
    setCallState('idle');
    setErrorMsg(null);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') handleInitiateCall();
  }

  const isDisabled = callState === 'calling' || !phoneNumber.trim();

  return (
    <div className="p-4 bg-[#070a12] border-b border-slate-800/80">
      {/* Section header */}
      <div className="flex items-center gap-2 mb-2.5">
        <div className="flex items-center justify-center w-5 h-5 rounded-md bg-purple-500/20 border border-purple-500/30">
          <Zap className="w-3 h-3 text-purple-300" />
        </div>
        <span className="text-[11px] font-bold text-purple-300 uppercase tracking-widest">
          Initiate AI Voice Call
        </span>
      </div>

      {/* Input + Button row */}
      <div className="flex gap-2">
        <input
          id="sip-phone-number-input"
          type="tel"
          placeholder="+14155552671"
          value={phoneNumber}
          onChange={(e) => {
            setPhoneNumber(e.target.value);
            if (callState === 'error') setCallState('idle');
          }}
          onKeyDown={handleKeyDown}
          disabled={callState === 'calling'}
          suppressHydrationWarning
          className="flex-1 bg-[#0b0f19] border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:border-purple-500/50 disabled:opacity-50"
        />

        <button
          id="trigger-ai-call-btn"
          onClick={handleInitiateCall}
          disabled={isDisabled}
          suppressHydrationWarning
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-gradient-to-r from-purple-600 via-pink-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-xs font-bold text-white transition-all shadow-md shadow-purple-900/20 flex-shrink-0"
        >
          {callState === 'calling' ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <PhoneCall className="w-3.5 h-3.5" />
          )}
          <span>{callState === 'calling' ? 'Dialing...' : 'Initiate AI Call'}</span>
        </button>
      </div>

      {/* Success state */}
      {callState === 'success' && result && (
        <div className="mt-2.5 rounded-lg border border-emerald-500/25 bg-emerald-500/8 p-2.5">
          <div className="flex items-center gap-1.5 mb-1.5">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
            <span className="text-[11px] font-semibold text-emerald-400">
              Call initiated successfully
            </span>
          </div>
          <div className="space-y-0.5">
            <p className="text-[10px] text-slate-400">
              <span className="text-slate-500">Call SID: </span>
              <span className="font-mono text-slate-300 break-all">
                {result.call_sid || '—'}
              </span>
            </p>
          </div>
        </div>
      )}

      {/* Error state */}
      {callState === 'error' && errorMsg && (
        <div className="mt-2.5 rounded-lg border border-red-500/25 bg-red-500/8 p-2.5">
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-start gap-1.5 min-w-0">
              <XCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0 mt-0.5" />
              <p className="text-[10px] text-red-300 leading-relaxed break-words min-w-0">
                {errorMsg}
              </p>
            </div>
            <button
              id="sip-error-dismiss-btn"
              onClick={handleDismissError}
              className="flex-shrink-0 text-slate-500 hover:text-slate-300 transition-colors"
              aria-label="Dismiss error"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
