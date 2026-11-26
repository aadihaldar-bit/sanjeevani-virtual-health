'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Headphones, Send, RefreshCw, Pause } from 'lucide-react';
import { synthesizeSpeechLive } from '@/lib/audioSamples';

type IndustryId = 'finance' | 'healthcare' | 'government';

interface Msg {
  role: 'caller' | 'agent';
  name: string;
  text: string;
}

interface Industry {
  id: IndustryId;
  tab: string;
  heading: string;
  subtitle: string;
  description: string;
  callerName: string;
  agentName: string;
  voice: string;
  room: number;
  opening: string;
  followUps: string[];
}

const INDUSTRIES: Industry[] = [
  {
    id: 'finance',
    tab: 'Financial services',
    heading: 'Financial services',
    subtitle: 'Back office operations',
    description:
      'Internal agent assist for reconciliation, exception handling, and compliance workflows — grounded in your own SOPs.',
    callerName: 'Kevin',
    agentName: 'Deepak',
    voice: 'Deepak',
    room: 2,
    opening: "Hey, I've got a customer worried about a wire that supposedly didn't land.",
    followUps: [
      'Can you check the status of reference WIRE-55820?',
      'What should I tell the customer about the timing?',
      'Please raise a ticket if it needs manual review.',
    ],
  },
  {
    id: 'healthcare',
    tab: 'Healthcare',
    heading: 'Healthcare services',
    subtitle: 'Telehealth concierge',
    description:
      'Pre-visit preparation, device and connection setup, and post-visit summaries — so clinical staff can focus on the cases that need a human.',
    callerName: 'Priya',
    agentName: 'Hope',
    voice: 'Hope',
    room: 0,
    opening: "I have a video consult at 3 but I can't get my camera to work.",
    followUps: [
      'I tried reconnecting and it still fails.',
      'Can you resend the appointment link?',
      'What else should I prepare before the call?',
    ],
  },
  {
    id: 'government',
    tab: 'Government',
    heading: 'Government',
    subtitle: 'Citizen services',
    description:
      'Voice agents that meet residents where they are, cut wait times on public services, and let staff focus on the cases that need a human.',
    callerName: 'Rahul',
    agentName: 'Aarohi',
    voice: 'Aarohi',
    room: 5,
    opening: 'I need to renew my parking permit before it expires tomorrow.',
    followUps: [
      'My permit number is PKG-4471.',
      'Can I pay the renewal fee over the phone?',
      'When will I get the confirmation?',
    ],
  },
];

/* ───────────────── Original isometric-style office illustration ───────────── */
const ROOMS = [
  { x: 60, y: 60 }, { x: 240, y: 60 }, { x: 420, y: 60 }, { x: 600, y: 60 },
  { x: 60, y: 238 }, { x: 240, y: 238 }, { x: 420, y: 238 }, { x: 600, y: 238 },
];
const RW = 160, RH = 150;

function Workstation({ x, y, person = true }: { x: number; y: number; person?: boolean }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      {/* desk */}
      <rect x="0" y="22" width="64" height="30" rx="3" />
      {/* monitor */}
      <rect x="18" y="4" width="28" height="18" rx="2" />
      <path d="M32 22 v4 M24 30 h16" />
      {/* keyboard */}
      <rect x="14" y="40" width="36" height="7" rx="1.5" />
      {/* chair */}
      <rect x="22" y="60" width="20" height="14" rx="4" />
      {person && (
        <g>
          <circle cx="32" cy="60" r="6" />
          <path d="M32 66 q-9 6 -9 14" />
          <path d="M32 66 q9 6 9 14" />
        </g>
      )}
    </g>
  );
}

function MeetingRoom({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x="20" y="30" width="100" height="40" rx="8" />
      {[18, 48, 78, 108].map((cx) => (
        <React.Fragment key={cx}>
          <rect x={cx} y="8" width="20" height="12" rx="3" />
          <rect x={cx} y="80" width="20" height="12" rx="3" />
        </React.Fragment>
      ))}
      <rect x="8" y="104" width="124" height="8" rx="2" />
    </g>
  );
}

function Reception({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <path d="M16 70 q0 -40 56 -40 q56 0 56 40" />
      <path d="M30 70 q0 -26 42 -26 q42 0 42 26" />
      <circle cx="72" cy="92" r="7" />
      <path d="M72 99 q-10 6 -10 16 M72 99 q10 6 10 16" />
    </g>
  );
}

function Lounge({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      {/* sofa */}
      <rect x="20" y="40" width="80" height="34" rx="12" />
      <rect x="14" y="48" width="12" height="26" rx="5" />
      <rect x="94" y="48" width="12" height="26" rx="5" />
      {/* coffee table */}
      <rect x="44" y="84" width="34" height="16" rx="3" />
      {/* plant */}
      <g transform="translate(118 70)">
        <path d="M0 24 h14 l-2 -16 h-10 z" />
        <path d="M7 8 q-8 -14 -3 -20 M7 8 q8 -14 3 -20 M7 8 q0 -16 0 -22" />
      </g>
    </g>
  );
}

function ServerRack({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      {[0, 1].map((c) => (
        <g key={c} transform={`translate(${30 + c * 54} 16)`}>
          <rect x="0" y="0" width="40" height="92" rx="4" />
          {[0, 1, 2, 3, 4].map((r) => (
            <g key={r}>
              <rect x="6" y={10 + r * 16} width="28" height="9" rx="1.5" />
              <circle cx="30" cy={14.5 + r * 16} r="1.4" />
            </g>
          ))}
        </g>
      ))}
    </g>
  );
}

function Pantry({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x="20" y="30" width="110" height="20" rx="3" />
      <rect x="28" y="12" width="16" height="18" rx="2" />
      <circle cx="70" cy="22" r="8" />
      <rect x="92" y="10" width="28" height="20" rx="2" />
      <path d="M30 70 h90" />
      <rect x="40" y="76" width="22" height="26" rx="2" />
      <rect x="78" y="76" width="22" height="26" rx="2" />
    </g>
  );
}

function IsoOffice({ room, onListen, listening }: { room: number; onListen: () => void; listening: boolean }) {
  return (
    <div className="relative">
      <svg
        viewBox="0 0 800 470"
        className="w-full h-auto text-stone-400 dark:text-stone-600"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="36" y="36" width="748" height="378" rx="16" strokeWidth="1.8" />
        <rect x="250" y="402" width="120" height="8" rx="2" />
        <rect x="430" y="402" width="120" height="8" rx="2" />

        {ROOMS.map((r, i) => (
          <rect key={i} x={r.x} y={r.y} width={RW} height={RH} rx="5" strokeWidth="1.1" className="text-stone-300 dark:text-stone-700" />
        ))}

        {/* highlighted active room */}
        <rect
          x={ROOMS[room].x}
          y={ROOMS[room].y}
          width={RW}
          height={RH}
          rx="5"
          className={`fill-indigo-500/12 stroke-indigo-500/70 dark:fill-indigo-400/10 ${listening ? 'animate-pulse' : ''}`}
          strokeWidth="2.4"
        />

        {/* furniture, room by room (original) */}
        <Workstation x={ROOMS[0].x + 44} y={ROOMS[0].y + 34} />
        <Workstation x={ROOMS[1].x + 44} y={ROOMS[1].y + 34} />
        <MeetingRoom x={ROOMS[2].x + 12} y={ROOMS[2].y + 18} />
        <Reception x={ROOMS[3].x + 14} y={ROOMS[3].y + 18} />
        <Pantry x={ROOMS[4].x + 10} y={ROOMS[4].y + 18} />
        <Lounge x={ROOMS[5].x + 12} y={ROOMS[5].y + 20} />
        <Workstation x={ROOMS[6].x + 44} y={ROOMS[6].y + 34} />
        <ServerRack x={ROOMS[7].x + 18} y={ROOMS[7].y + 18} />
      </svg>

      {/* Listen-in hotspot over the active room */}
      <button
        onClick={onListen}
        style={{
          left: `${((ROOMS[room].x + RW / 2) / 800) * 100}%`,
          top: `${((ROOMS[room].y + RH / 2) / 470) * 100}%`,
        }}
        className="absolute -translate-x-1/2 -translate-y-1/2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white dark:bg-stone-900 border border-indigo-500/50 shadow-lg text-[11px] font-semibold text-indigo-700 dark:text-indigo-300 hover:scale-105 active:scale-95 transition-all cursor-pointer disabled:opacity-60"
        disabled={listening}
      >
        {listening ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Headphones className="w-3 h-3" />}
        {listening ? 'Listening…' : 'Listen in'}
      </button>
    </div>
  );
}

/* ─────────────────────────── Live interactive section ─────────────────────── */
export function IndustryShowcase() {
  const [active, setActive] = useState<IndustryId>('finance');
  const industry = INDUSTRIES.find((i) => i.id === active) || INDUSTRIES[0];

  const [messages, setMessages] = useState<Msg[]>([{ role: 'caller', name: industry.callerName, text: industry.opening }]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [playingBack, setPlayingBack] = useState(false);
  const [followIdx, setFollowIdx] = useState(0);
  const nodeRef = useRef<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);

  // reset conversation when the industry changes
  useEffect(() => {
    stopAudio();
    setMessages([{ role: 'caller', name: industry.callerName, text: industry.opening }]);
    setFollowIdx(0);
    nodeRef.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [messages, loading]);

  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel();
    setPlayingBack(false);
  };

  const playAudio = async (dataUri: string | undefined, fallbackText: string) => {
    stopAudio();
    setPlayingBack(true);
    if (dataUri) {
      const audio = new Audio(dataUri);
      audioRef.current = audio;
      audio.onended = () => setPlayingBack(false);
      audio.onerror = () => setPlayingBack(false);
      audio.play().catch(() => setPlayingBack(false));
    } else {
      const audio = await synthesizeSpeechLive(fallbackText, industry.voice);
      if (audio) {
        audioRef.current = audio;
        audio.onended = () => setPlayingBack(false);
        audio.onerror = () => setPlayingBack(false);
        audio.play().catch(() => setPlayingBack(false));
      } else setPlayingBack(false);
    }
  };

  // Core: send a human line to the real agent and append its reply
  const sendToAgent = async (text: string) => {
    const human = text.trim();
    if (!human || loading) return;

    const history = messages.map((m) => ({ role: m.role === 'caller' ? 'user' : 'assistant', content: m.text }));
    setMessages((prev) => [...prev, { role: 'caller', name: industry.callerName, text: human }]);
    setInput('');
    setLoading(true);

    try {
      const res = await fetch('/api/simulator/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_message: human,
          message: human,
          current_node_id: nodeRef.current,
          history,
        }),
      });
      if (!res.ok) throw new Error('bad status');
      const data = await res.json();
      const reply = data.text || data.response || 'Let me look into that and get right back to you.';
      if (data.next_node_id) nodeRef.current = data.next_node_id;
      setMessages((prev) => [...prev, { role: 'agent', name: industry.agentName, text: reply }]);
      playAudio(data.audio_base64, reply);
    } catch {
      // Graceful local fallback (e.g. when the backend isn't reachable from this host)
      const reply =
        'I understand — let me pull that up and walk you through the next step so we can get this resolved for you.';
      setMessages((prev) => [...prev, { role: 'agent', name: industry.agentName, text: reply }]);
      playAudio(undefined, reply);
    } finally {
      setLoading(false);
    }
  };

  // "Listen in" / suggested advance: send the next scripted human follow-up
  const handleListenIn = () => {
    if (loading) return;
    const next = industry.followUps[followIdx % industry.followUps.length];
    setFollowIdx((i) => i + 1);
    sendToAgent(next);
  };

  return (
    <section className="w-full border-t border-stone-200/80 dark:border-white/5">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-10">
          <h2 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-stone-900 dark:text-white">
            Voice agents for <span className="text-indigo-600 dark:text-indigo-400">every industry</span>
          </h2>
          <div className="inline-flex p-1 rounded-full border border-stone-200 dark:border-white/10 bg-stone-50 dark:bg-stone-900/60 self-start sm:self-auto">
            {INDUSTRIES.map((ind) => (
              <button
                key={ind.id}
                onClick={() => setActive(ind.id)}
                className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all cursor-pointer ${
                  active === ind.id
                    ? 'bg-white dark:bg-stone-800 text-stone-900 dark:text-white shadow-sm'
                    : 'text-stone-500 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white'
                }`}
              >
                {ind.tab}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 items-start">
          {/* Left: live conversation */}
          <div>
            <h3 className="text-lg font-bold text-stone-900 dark:text-white">{industry.heading}</h3>
            <p className="mt-5 text-sm font-semibold text-stone-800 dark:text-stone-200">{industry.subtitle}</p>
            <p className="mt-2 text-sm text-stone-600 dark:text-stone-400 leading-relaxed max-w-md">{industry.description}</p>

            {/* conversation thread */}
            <div
              ref={threadRef}
              className="mt-6 max-w-md space-y-3 max-h-[260px] overflow-y-auto pr-1 scroll-smooth"
            >
              {messages.map((m, idx) => {
                const isAgent = m.role === 'agent';
                return (
                  <div key={idx} className={`flex flex-col ${isAgent ? 'items-start' : 'items-end'}`}>
                    <span className="text-[11px] text-stone-400 mb-1 px-1">{m.name}</span>
                    <div
                      className={`px-4 py-2.5 rounded-2xl text-xs leading-relaxed ${
                        isAgent
                          ? 'bg-stone-100 dark:bg-stone-800 text-stone-800 dark:text-stone-200 border border-stone-200/60 dark:border-white/5'
                          : 'bg-indigo-600 text-white'
                      }`}
                    >
                      {m.text}
                    </div>
                  </div>
                );
              })}
              {loading && (
                <div className="flex items-center gap-2 px-1">
                  <span className="text-[11px] font-mono uppercase tracking-wider text-stone-400">{industry.agentName}</span>
                  <span className="flex gap-1">
                    {[0, 1, 2, 3].map((d) => (
                      <span key={d} className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse" style={{ animationDelay: `${d * 0.15}s` }} />
                    ))}
                  </span>
                </div>
              )}
            </div>

            {/* suggested follow-up chips */}
            <div className="mt-4 flex flex-wrap gap-2 max-w-md">
              {industry.followUps.map((f) => (
                <button
                  key={f}
                  onClick={() => sendToAgent(f)}
                  disabled={loading}
                  className="px-3 py-1.5 rounded-full border border-stone-200 dark:border-white/10 text-[11px] text-stone-600 dark:text-stone-300 hover:border-indigo-400 hover:text-indigo-600 dark:hover:text-indigo-400 transition-all cursor-pointer disabled:opacity-50"
                >
                  {f}
                </button>
              ))}
            </div>

            {/* free-text input */}
            <div className="mt-4 flex items-center gap-2 max-w-md">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && sendToAgent(input)}
                placeholder={`Ask ${industry.agentName} something…`}
                className="flex-1 rounded-full border border-stone-200 dark:border-white/10 bg-white dark:bg-stone-900 px-4 py-2.5 text-xs text-stone-900 dark:text-white focus:outline-none focus:border-indigo-400"
              />
              <button
                onClick={() => sendToAgent(input)}
                disabled={loading || !input.trim()}
                className="w-10 h-10 rounded-full bg-indigo-600 text-white flex items-center justify-center hover:bg-indigo-700 active:scale-95 transition-all cursor-pointer disabled:opacity-50"
              >
                {loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              </button>
              {playingBack && (
                <button
                  onClick={stopAudio}
                  className="w-10 h-10 rounded-full border border-stone-200 dark:border-white/10 text-stone-600 dark:text-stone-300 flex items-center justify-center hover:bg-stone-50 dark:hover:bg-white/5 transition-all cursor-pointer"
                  title="Stop audio"
                >
                  <Pause className="w-4 h-4 fill-current" />
                </button>
              )}
            </div>
            <p className="mt-2 text-[11px] text-stone-400 max-w-md">
              Live demo — responses come from the STL agent and are grounded in your SOPs.
            </p>
          </div>

          {/* Right: interactive office */}
          <div className="rounded-3xl border border-stone-200/80 dark:border-white/10 bg-stone-50/50 dark:bg-[#0d0f17] p-4 sm:p-6">
            <IsoOffice room={industry.room} onListen={handleListenIn} listening={loading} />
          </div>
        </div>
      </div>
    </section>
  );
}
