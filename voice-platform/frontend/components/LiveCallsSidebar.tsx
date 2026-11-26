'use client';

import React, { useEffect, useState } from 'react';
import { Phone, ArrowUpRight, PhoneOutgoing, Loader2, CheckCircle2, XCircle, X } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import { initiateOutboundCall } from '@/lib/api';
import { Pulse } from './Pulse';
import { agentLabel, cleanNumber, isHandedOver } from './ConversationView';
import { useWorkspace } from '@/lib/useMe';

export function LiveCallsSidebar() {
  const { activeCalls, selectedCallId, selectCall } = useDashboardStore();
  const { isPlatform } = useWorkspace();           // placing a call from the console uses the platform's number

  const [toNumber, setToNumber] = useState('');
  const [isCalling, setIsCalling] = useState(false);
  const [callSuccess, setCallSuccess] = useState<string | null>(null);
  const [callError, setCallError] = useState<string | null>(null);
  const [, setNow] = useState(Date.now());

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  function formatElapsed(startedAt: string | null): string {
    if (!startedAt) return '—';
    const utcIso = startedAt.endsWith('Z') || startedAt.includes('+') ? startedAt : startedAt + 'Z';
    const diff = Math.floor((Date.now() - new Date(utcIso).getTime()) / 1000);
    if (Number.isNaN(diff)) return '—';
    const elapsed = Math.max(0, diff);          // a server clock a little ahead of this browser's would read negative
    const m = Math.floor(elapsed / 60);
    const s = elapsed % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  async function handleCall() {
    const trimmed = toNumber.trim();
    if (!trimmed) return;

    setIsCalling(true);
    setCallSuccess(null);
    setCallError(null);

    try {
      await initiateOutboundCall(trimmed);
      setCallSuccess(`Dialed ${trimmed}`);
      setToNumber('');
      setTimeout(() => setCallSuccess(null), 6000);
    } catch (err: any) {
      console.error('Call failed', err);
      setCallError(err?.message || 'Failed to initiate outbound call');
    } finally {
      setIsCalling(false);
    }
  }

  function formatLiveStatus(status: string | null | undefined): string {
    if (!status) return 'active';
    return status.replace(/_/g, ' ');
  }

  function statusTone(status: string | null | undefined): string {
    switch (status) {
      case 'initiated':
        return 'bg-sky-50 text-sky-700 border-sky-200';
      case 'ringing':
        return 'bg-amber-50 text-amber-700 border-amber-200';
      case 'answered':
      case 'in-progress':
      case 'in_progress':
      case 'active':
      case 'started':
        return 'bg-emerald-50 text-emerald-800 border-emerald-200';
      default:
        return 'bg-stone-100 text-stone-600 border-stone-200';
    }
  }

  return (
    <div className="w-72 xl:w-80 h-full flex flex-col bg-white dark:bg-[#0c0e14] border-r border-stone-200/90 dark:border-white/10 flex-shrink-0 transition-colors duration-300">
      {/* Header */}
      <div className="p-4 border-b border-stone-200/80 dark:border-white/10 bg-stone-50/70 dark:bg-[#111420]/80">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Phone className="w-4 h-4 text-stone-800 dark:text-stone-200 stroke-[1.5]" />
            <h2 className="text-[13px] font-semibold text-stone-900 dark:text-white">Active calls</h2>
          </div>
          <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${
            activeCalls.length > 0
              ? 'bg-emerald-50 dark:bg-emerald-950/50 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800/60'
              : 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 border-stone-200 dark:border-white/10'
          }`}>
            {activeCalls.length} active
          </span>
        </div>

        {/* Outbound dialer (platform staff only) */}
        {isPlatform && <div className="space-y-2">
          <div className="flex gap-1.5">
            <input
              type="tel"
              placeholder="+91 97643 67506"
              value={toNumber}
              onChange={(e) => {
                setToNumber(e.target.value);
                if (callError) setCallError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCall();
              }}
              className="flex-1 bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-stone-900 dark:text-white placeholder-stone-400 dark:placeholder-stone-500 focus:outline-none focus:border-stone-500 dark:focus:border-white/30 shadow-2xs"
            />
            <button
              onClick={handleCall}
              disabled={!toNumber.trim() || isCalling}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-stone-900 dark:bg-indigo-600 hover:bg-stone-800 dark:hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-xs font-semibold text-white transition-all shadow-xs cursor-pointer"
            >
              {isCalling ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <PhoneOutgoing className="w-3.5 h-3.5" />
              )}
              Dial
            </button>
          </div>

          {/* Feedback messages */}
          {callSuccess && (
            <div className="flex items-center gap-1.5 p-2 rounded-lg bg-emerald-50 dark:bg-emerald-950/50 border border-emerald-200 dark:border-emerald-800/60 text-emerald-800 dark:text-emerald-300 text-[11px]">
              <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" />
              <span className="truncate">{callSuccess}</span>
            </div>
          )}

          {callError && (
            <div className="flex items-start justify-between gap-1.5 p-2 rounded-lg bg-rose-50 dark:bg-rose-950/50 border border-rose-200 dark:border-rose-800/60 text-rose-800 dark:text-rose-300 text-[11px]">
              <div className="flex items-start gap-1 min-w-0">
                <XCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span className="break-words min-w-0">{callError}</span>
              </div>
              <button onClick={() => setCallError(null)} className="text-rose-400 hover:text-rose-600 dark:hover:text-rose-300">
                <X className="w-3 h-3" />
              </button>
            </div>
          )}
        </div>}
      </div>

      {/* Active Calls List */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2 bg-[#fbfbfa] dark:bg-[#08090e]">
        {activeCalls.length === 0 ? (
          <div className="p-8 text-center">
            <div className="w-10 h-10 mx-auto mb-2 rounded-full bg-stone-100 dark:bg-stone-800/60 flex items-center justify-center">
              <Phone className="w-5 h-5 text-stone-400 dark:text-stone-500 stroke-[1.5]" />
            </div>
            <p className="text-stone-800 dark:text-stone-200 text-xs font-semibold">No active calls right now</p>
            <p className="text-stone-500 dark:text-stone-400 text-[11px] mt-1 leading-relaxed">
              Inbound SIP calls and outbound tests will stream here live.
            </p>
          </div>
        ) : (
          activeCalls.map((call) => (
            <button
              key={call.id}
              onClick={() => selectCall(call.id)}
              className={`w-full text-left p-3 rounded-xl transition-all border cursor-pointer ${
                selectedCallId === call.id
                  ? 'bg-stone-100/90 dark:bg-white/10 border-stone-400 dark:border-white/30 shadow-xs ring-1 ring-stone-400/30 dark:ring-white/20'
                  : 'bg-white dark:bg-[#111420] border-stone-200 dark:border-white/10 hover:bg-stone-50 dark:hover:bg-white/5 hover:border-stone-300 dark:hover:border-white/20 shadow-2xs'
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-start gap-2.5 min-w-0 flex-1">
                  <Pulse active={true} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between mb-1">
                      <p className="text-xs font-mono font-bold text-stone-900 dark:text-white truncate">
                        {call.source === 'simulator' || call.caller_number === 'Web Simulator'
                          ? `Web Simulator #${call.id}`
                          : (cleanNumber(call.caller_number) || 'Unknown Caller')}
                      </p>
                      <span className="text-[10px] font-mono text-stone-500 dark:text-stone-400">
                        {formatElapsed(call.started_at)}
                      </span>
                    </div>

                    <div className="flex flex-wrap items-center gap-1.5 mt-1">
                      <span className={`inline-flex items-center rounded-md border px-1.5 py-0.2 text-[9px] capitalize font-semibold ${statusTone(call.live_status)}`}>
                        {formatLiveStatus(call.live_status)}
                      </span>
                      {(call.source === 'simulator' || call.caller_number === 'Web Simulator') && (
                        <span className="inline-flex items-center rounded-md border px-1.5 py-0.2 text-[9px] font-semibold border-sky-300 dark:border-sky-700 bg-sky-50 dark:bg-sky-950/50 text-sky-800 dark:text-sky-300">
                          Test session
                        </span>
                      )}
                      {isHandedOver(call) && (
                        <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.2 rounded-md bg-amber-50 dark:bg-amber-950/50 border border-amber-200 dark:border-amber-800/60 text-amber-800 dark:text-amber-300 font-bold">
                          <ArrowUpRight className="w-2.5 h-2.5" />
                          Handed over
                        </span>
                      )}
                    </div>

                    {agentLabel(call.current_agent) && (
                      <p className="text-[10px] text-stone-700 dark:text-stone-300 font-medium mt-1.5 truncate">
                        Subagent: {agentLabel(call.current_agent)}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
