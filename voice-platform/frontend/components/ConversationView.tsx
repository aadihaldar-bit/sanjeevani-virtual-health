'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Mic, Clock, ArrowLeft, ArrowUpRight, CheckCircle2, User, Bot, Sparkles } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import { fetchCallDetail, analyzeTranscript } from '@/lib/api';
import type { CallData, TranscriptMessage, CallAnalysis } from '@/lib/api';
import { SecurityPanel } from '@/components/SecurityBadge';

// ─── Small formatters shared by the call screens (History, Live, this view) ─────

/** m:ss for a call's length; a dash when none was recorded (null or 0). */
export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return '—';
  const total = Math.round(seconds);            // round first, so 239.81s reads 4:00 and never 3:60
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/** A number or name as people should read it. Some numbers arrive with the "+" turned into a space (" 918710555635"). */
export function cleanNumber(raw: string | null | undefined): string {
  if (!raw || !raw.trim()) return '';
  return /^\s+\d+$/.test(raw) ? `+${raw.trim()}` : raw.trim();
}

/** A call's topic as readable text. "unknown" means none was worked out; snake_case ("network_printer") reads as words. */
export function topicLabel(raw: string | null | undefined): string {
  const s = (raw || '').replace(/_/g, ' ').trim();
  if (!s || s.toLowerCase() === 'unknown') return '';
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** The agent's name. "custom-agent" is the id a user-built agent is saved under, not a name. */
export function agentLabel(raw: string | null | undefined): string {
  const s = (raw || '').trim();
  return s === 'custom-agent' ? 'Custom agent' : s;
}

/** Handed to a person. The live pipeline sets transferred_to_human; is_escalated comes from the older webhook path. */
export function isHandedOver(call: Pick<CallData, 'is_escalated' | 'transferred_to_human'>): boolean {
  return !!(call.is_escalated || call.transferred_to_human);
}

/** Tickets carry "P1", "P1-Critical" and "critical" for the same thing; show one form. */
function priorityLabel(raw: string | null | undefined): string {
  const s = (raw || '').trim();
  if (/^(p1|critical)/i.test(s)) return 'P1-Critical';
  if (/^(p2|high)/i.test(s)) return 'P2-High';
  if (/^(p3|medium)/i.test(s)) return 'P3-Medium';
  return s;
}

/** What was actually said. Some agent turns carry the model's reasoning (<think>…</think>) and **bold** markers. */
function spokenText(raw: string | null | undefined): string {
  return (raw || '')
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<think>[\s\S]*$/i, '')              // a reasoning block that was never closed
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .trim();
}

/** A link the agent has given the caller (an upload or payment page) is shown as a link a person can open, for the demo. */
function linkify(text: string): React.ReactNode[] {
  return text.split(/(https?:\/\/[^\s]+)/g).map((part, i) =>
    /^https?:\/\//.test(part)
      ? <a key={i} href={part} target="_blank" rel="noopener noreferrer" className="text-indigo-600 dark:text-indigo-400 underline break-all">{part}</a>
      : part);
}

interface Props {
  /** Where "back" goes. Default: clear the selection, which returns the Live tab to its overview. */
  onBack?: () => void;
  backLabel?: string;
}

export function ConversationView({ onBack, backLabel = 'Back to live' }: Props = {}) {
  const { selectedCallId, selectedCallDetail, setCallDetail, selectCall } = useDashboardStore();
  const [analysis, setAnalysis] = useState<CallAnalysis | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const transcriptContainerRef = useRef<HTMLDivElement>(null);

  const scrollToTop = () => {
    transcriptContainerRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const scrollToBottom = () => {
    if (transcriptContainerRef.current) {
      transcriptContainerRef.current.scrollTo({ top: transcriptContainerRef.current.scrollHeight, behavior: 'smooth' });
    }
  };

  // Load call detail when selected. A reply for a call that is no longer selected is dropped.
  useEffect(() => {
    setAnalysis(null);
    setLoadFailed(false);
    if (!selectedCallId) {
      setCallDetail(null);
      return;
    }
    let cancelled = false;
    fetchCallDetail(selectedCallId)
      .then((detail) => { if (!cancelled) setCallDetail(detail); })
      .catch((err) => {
        console.error('Failed to load call detail:', err);
        if (!cancelled) setLoadFailed(true);
      });
    return () => { cancelled = true; };
  }, [selectedCallId]);

  // Auto-scroll on new messages container only
  useEffect(() => {
    if (transcriptContainerRef.current) {
      transcriptContainerRef.current.scrollTo({ top: transcriptContainerRef.current.scrollHeight, behavior: 'smooth' });
    }
  }, [selectedCallDetail?.transcript]);

  const goBack = onBack ?? (() => selectCall(null));

  const handleAnalyze = async () => {
    if (!selectedCallId) return;
    setIsAnalyzing(true);
    try {
      const res = await analyzeTranscript(selectedCallId);
      setAnalysis(res);
    } catch (err) {
      console.error('Failed to analyze transcript:', err);
      alert('Failed to analyze transcript');
    } finally {
      setIsAnalyzing(false);
    }
  };

  const backButton = (
    <button
      onClick={goBack}
      className="mb-2 inline-flex items-center gap-1 text-xs font-medium text-stone-500 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white cursor-pointer"
    >
      <ArrowLeft className="w-3.5 h-3.5" />
      {backLabel}
    </button>
  );

  // Empty state
  if (!selectedCallId) {
    return (
      <div className="flex-1 flex items-center justify-center bg-[#fbfbfa] dark:bg-[#08090e] transition-colors duration-300">
        <div className="text-center p-8">
          <Mic className="w-10 h-10 text-stone-300 dark:text-stone-600 mx-auto mb-3 stroke-[1.5]" />
          <p className="text-stone-700 dark:text-stone-300 text-sm font-semibold">Select a call to view transcript</p>
          <p className="text-stone-400 dark:text-stone-500 text-xs mt-1">
            Click any active or completed interaction from the roster
          </p>
        </div>
      </div>
    );
  }

  // Loading state
  if (!selectedCallDetail) {
    return (
      <div className="flex-1 flex items-center justify-center bg-[#fbfbfa] dark:bg-[#08090e] transition-colors duration-300">
        <div className="text-center">
          {loadFailed ? (
            <>
              <p className="text-stone-700 dark:text-stone-300 text-sm font-semibold">Could not load this call</p>
              <p className="text-stone-400 dark:text-stone-500 text-xs mt-1 mb-3">It may have been removed, or the server did not answer.</p>
              {backButton}
            </>
          ) : (
            <>
              <div className="w-7 h-7 border-2 border-stone-200 dark:border-white/10 border-t-stone-800 dark:border-t-white rounded-full animate-spin mx-auto mb-3" />
              <p className="text-stone-500 dark:text-stone-400 text-xs font-medium">Loading call details...</p>
            </>
          )}
        </div>
      </div>
    );
  }

  const call = selectedCallDetail;
  // Turns as people read them: reasoning-only turns drop out, so the count and "Turn #" match what is on screen.
  const turns = (call.transcript ?? []).map((m) => ({ ...m, message: spokenText(m.message) })).filter((m) => m.message);

  // Extract ticket details if present
  let detectedTicketId = call.latest_ticket?.ticket_id;
  let detectedPriority = priorityLabel(call.latest_ticket?.priority) || 'P1-Critical';
  let detectedIssue = call.latest_ticket?.issue || '';
  let detectedIntent = topicLabel(call.latest_ticket?.intent) || 'Incident Management System';

  if (!detectedTicketId && call.transcript) {
    for (const msg of call.transcript) {
      if (!msg || !msg.message) continue;
      const match = msg.message.match(/(SYM-INC-[0-9\-]+|TKT-[0-9]+|MSEDCL-[0-9\-]+)/i);
      if (match) {
        detectedTicketId = match[1];
        if (msg.message.includes('P1') || msg.message.includes('Critical')) detectedPriority = 'P1-Critical';
        else if (msg.message.includes('P2') || msg.message.includes('High')) detectedPriority = 'P2-High';
        else if (msg.message.includes('P3') || msg.message.includes('Medium')) detectedPriority = 'P3-Medium';
        break;
      }
    }
  }

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-[#fbfbfa] dark:bg-[#08090e] transition-colors duration-300">
      {/* Header */}
      <div className="p-4 border-b border-stone-200/90 dark:border-white/10 bg-white dark:bg-[#0d0f17] shadow-2xs flex-shrink-0 max-h-[50vh] overflow-y-auto">
        {backButton}
        <div className="flex items-center justify-between mb-2">
          <div>
            <h2 className="text-base font-bold text-stone-900 dark:text-white font-mono tracking-tight">
              {cleanNumber(call.caller_number) || 'Unknown Caller'}
            </h2>
            <div className="flex items-center gap-3 mt-1">
              <span className="flex items-center gap-1 text-xs text-stone-500 dark:text-stone-400 font-medium">
                <Clock className="w-3.5 h-3.5" />
                {formatDuration(call.duration_seconds)}
              </span>
              {call.status === 'active' && (
                <span className="flex items-center gap-1 text-xs text-emerald-800 dark:text-emerald-300 font-medium">
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  Active Live
                </span>
              )}
              {call.status === 'completed' && (
                <span className="flex items-center gap-1 text-xs text-stone-500 dark:text-stone-400 font-medium">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Completed
                </span>
              )}
              {agentLabel(call.current_agent) && (
                <span className="text-xs text-stone-800 dark:text-stone-300 font-semibold px-2 py-0.5 rounded-md bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-white/10">
                  Subagent: {agentLabel(call.current_agent)}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleAnalyze}
              disabled={isAnalyzing || turns.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-stone-900 dark:bg-indigo-600 text-white hover:bg-stone-800 dark:hover:bg-indigo-500 disabled:opacity-50 transition-colors shadow-xs cursor-pointer"
            >
              {isAnalyzing ? (
                <div className="w-3 h-3 border-2 border-stone-400 border-t-white rounded-full animate-spin" />
              ) : (
                <Sparkles className="w-3.5 h-3.5 text-stone-300 stroke-[1.5]" />
              )}
              {isAnalyzing ? 'Analyzing...' : 'AI Analyze'}
            </button>
            {isHandedOver(call) && (
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-800/60 bg-amber-50 dark:bg-amber-950/50">
                <ArrowUpRight className="w-3.5 h-3.5" />
                Handed over
              </span>
            )}
            {call.sentiment && (
              <span className="px-2.5 py-1 rounded-full text-xs font-medium capitalize text-stone-700 dark:text-stone-300 border border-stone-300 dark:border-white/10 bg-stone-50 dark:bg-white/5">
                {call.sentiment}
              </span>
            )}
          </div>
        </div>

        {/* Ticket Details Card */}
        {detectedTicketId && (
          <div className="mt-2.5 p-3 rounded-xl bg-white dark:bg-[#111420] border border-stone-200 dark:border-white/10 shadow-2xs">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="px-2.5 py-1 rounded-md text-xs font-mono font-bold border border-stone-300 dark:border-white/20 text-stone-900 dark:text-white bg-stone-50 dark:bg-white/5">
                  {detectedTicketId}
                </span>
                <span className="px-2 py-0.5 rounded-md text-[11px] font-bold text-stone-700 dark:text-stone-300 border border-stone-300 dark:border-white/10">
                  {detectedPriority}
                </span>
              </div>
              <span className="text-xs font-semibold text-stone-600 dark:text-stone-400 px-2.5 py-0.5 rounded-full border border-stone-200 dark:border-white/10">
                {detectedIntent}
              </span>
            </div>
            {detectedIssue && (
              <p className="text-xs text-stone-800 dark:text-stone-300 mt-2 font-medium break-words">
                <strong className="text-stone-900 dark:text-white font-semibold">Incident Issue:</strong> {detectedIssue}
              </p>
            )}
          </div>
        )}

        {/* Summary */}
        {call.summary && (
          <div className="mt-2.5 p-3 rounded-xl bg-stone-50 dark:bg-[#111420] border border-stone-200 dark:border-white/10">
            <p className="text-xs text-stone-700 dark:text-stone-300 leading-relaxed break-words">{call.summary}</p>
          </div>
        )}

        {/* Security guard (jailbreak / prompt-injection checks) */}
        {call.security && call.security.guard_events?.length > 0 && (
          <div className="mt-2.5 p-3 rounded-xl bg-white dark:bg-[#111420] border border-stone-200 dark:border-white/10">
            <p className="text-[10px] text-stone-400 font-medium mb-1.5 uppercase tracking-wider">Security Guard</p>
            <SecurityPanel security={call.security} />
          </div>
        )}

        {/* AI Analysis Panel */}
        {analysis && (
          <div className="mt-3 p-4 rounded-2xl bg-white dark:bg-[#111420] border border-stone-200 dark:border-white/10 shadow-2xs">
            <h3 className="text-xs font-bold text-stone-900 dark:text-white flex items-center gap-1.5 mb-2.5">
              <Sparkles className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400 stroke-[1.5]" />
              AI Conversation Insights
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
              <div className="p-2 rounded-xl bg-stone-50 dark:bg-white/5 border border-stone-100 dark:border-white/5">
                <p className="text-[10px] text-stone-400 font-medium mb-0.5">Sentiment</p>
                <div className="flex items-center gap-1">
                  <span className={`text-xs font-bold ${
                    analysis.sentiment === 'positive' ? 'text-emerald-700 dark:text-emerald-400' :
                    analysis.sentiment === 'negative' ? 'text-rose-700 dark:text-rose-400' :
                    'text-stone-800 dark:text-stone-200'
                  }`}>
                    {analysis.sentiment.charAt(0).toUpperCase() + analysis.sentiment.slice(1)}
                  </span>
                  <span className="text-[10px] text-stone-400 font-mono">({analysis.sentiment_score}/100)</span>
                </div>
              </div>
              <div className="p-2 rounded-xl bg-stone-50 dark:bg-white/5 border border-stone-100 dark:border-white/5">
                <p className="text-[10px] text-stone-400 font-medium mb-0.5">Emotion</p>
                <p className="text-xs font-semibold text-stone-800 dark:text-stone-200">{analysis.customer_emotion}</p>
              </div>
              <div className="p-2 rounded-xl bg-stone-50 dark:bg-white/5 border border-stone-100 dark:border-white/5">
                <p className="text-[10px] text-stone-400 font-medium mb-0.5">Escalation Risk</p>
                <span className={`inline-flex px-1.5 py-0.2 rounded text-[10px] font-bold ${
                  analysis.escalation_risk === 'high' ? 'bg-rose-50 dark:bg-rose-950/50 text-rose-800 dark:text-rose-300 border border-rose-200 dark:border-rose-800/60' :
                  analysis.escalation_risk === 'medium' ? 'bg-amber-50 dark:bg-amber-950/50 text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-800/60' :
                  'bg-emerald-50 dark:bg-emerald-950/50 text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800/60'
                }`}>
                  {analysis.escalation_risk.toUpperCase()}
                </span>
              </div>
              <div className="p-2 rounded-xl bg-stone-50 dark:bg-white/5 border border-stone-100 dark:border-white/5">
                <p className="text-[10px] text-stone-400 font-medium mb-0.5">Confidence</p>
                <p className="text-xs font-bold text-stone-800 dark:text-stone-200">{analysis.confidence}%</p>
              </div>
            </div>
            {analysis.key_issues && analysis.key_issues.length > 0 && (
              <div className="mb-2">
                <p className="text-[10px] text-stone-400 font-medium mb-1">Key Issues Identified</p>
                <div className="flex flex-wrap gap-1.5">
                  {analysis.key_issues.map((issue, idx) => (
                    <span key={idx} className="px-2 py-0.5 bg-stone-100 dark:bg-stone-800 rounded-md text-[10px] text-stone-800 dark:text-stone-200 font-medium border border-stone-200 dark:border-white/10">
                      {issue}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {analysis.summary && (
              <div className="pt-2 border-t border-stone-100 dark:border-white/10">
                <p className="text-[10px] text-stone-400 font-medium mb-0.5">Executive Summary</p>
                <p className="text-xs text-stone-700 dark:text-stone-300 leading-relaxed">{analysis.summary}</p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Transcript Messages Container */}
      <div 
        ref={transcriptContainerRef} 
        className="flex-1 min-h-0 overflow-y-auto p-6 space-y-4 relative scroll-smooth bg-[#fbfbfa] dark:bg-[#08090e]"
        style={{ height: 'calc(100vh - 160px)', overflowY: 'auto' }}
      >
        {turns.length === 0 ? (
          <div className="flex items-center justify-center h-full text-center">
            <p className="text-stone-400 dark:text-stone-500 text-xs">
              {call.status === 'active' ? 'No transcript messages recorded yet' : 'No transcript was recorded for this call'}
            </p>
          </div>
        ) : (
          turns.map((msg, index) => (
            <MessageBubble key={index} message={msg} turn={index + 1} />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Footer */}
      <div className="p-3.5 border-t border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#0d0f17] shadow-2xs flex-shrink-0 flex items-center justify-between">
        <div className="flex items-center gap-3 text-xs text-stone-500 dark:text-stone-400 font-mono">
          <span>{turns.length} turns recorded</span>
          <span>
            {call.conversation_id ? `Conv: ${call.conversation_id.slice(0, 16)}${call.conversation_id.length > 16 ? '...' : ''}` : ''}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button 
            onClick={scrollToTop}
            className="px-2.5 py-1 text-[11px] font-medium bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-300 rounded-md border border-stone-300 dark:border-white/10 transition-colors cursor-pointer"
          >
            ↑ Top
          </button>
          <button 
            onClick={scrollToBottom}
            className="px-2.5 py-1 text-[11px] font-medium bg-stone-900 dark:bg-indigo-600 hover:bg-stone-800 dark:hover:bg-indigo-500 text-white rounded-md transition-colors cursor-pointer"
          >
            ↓ Bottom
          </button>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ message, turn }: { message: TranscriptMessage; turn: number }) {
  const isAgent = message.role === 'agent';

  return (
    <div className={`flex ${isAgent ? 'justify-start' : 'justify-end'}`}>
      <div className={`flex items-start gap-2.5 max-w-[78%] ${isAgent ? '' : 'flex-row-reverse'}`}>
        {/* Avatar */}
        <div
          className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5 border ${
            isAgent
              ? 'bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300 border-stone-300 dark:border-white/10'
              : 'bg-white dark:bg-stone-800 text-stone-500 dark:text-stone-400 border-stone-200 dark:border-white/10'
          }`}
        >
          {isAgent ? (
            <Bot className="w-3.5 h-3.5 stroke-[1.5]" />
          ) : (
            <User className="w-3.5 h-3.5 stroke-[1.5]" />
          )}
        </div>

        {/* Message */}
        <div
          className={`min-w-0 px-4 py-3 rounded-2xl ${
            isAgent
              ? 'bg-white dark:bg-[#111420] text-stone-900 dark:text-stone-100 border border-stone-200/90 dark:border-white/10 shadow-2xs rounded-tl-xs'
              : 'bg-stone-50 dark:bg-[#181c2b] text-stone-800 dark:text-stone-200 border border-stone-200 dark:border-white/10 shadow-2xs rounded-tr-xs'
          }`}
        >
          <p className="text-xs leading-relaxed font-sans whitespace-pre-wrap break-words">{linkify(message.message)}</p>
          <p
            className="text-[9px] mt-1.5 font-medium text-stone-400 dark:text-stone-500"
          >
            {message.role === 'agent' ? 'Voice Agent' : 'Caller'} · Turn #{turn}
          </p>
        </div>
      </div>
    </div>
  );
}
