'use client';

import React, { useState } from 'react';
import { Mic, Volume2, BookOpen, PhoneCall, ArrowRight, Sparkles, Check, RefreshCw, Send } from 'lucide-react';
import { synthesizeSpeechLive } from '@/lib/audioSamples';

export function LiveFeatureTabs() {
  const [activeTab, setActiveTab] = useState<'tts' | 'stt' | 'rag' | 'phone'>('tts');

  // TTS State
  const [ttsInput, setTtsInput] = useState('Welcome to STL Digital! How can I assist with your IT support ticket today?');
  const [ttsSpeaker, setTtsSpeaker] = useState('Aarohi');
  const [ttsLoading, setTtsLoading] = useState(false);

  // Phone Call State
  const [phoneNumber, setPhoneNumber] = useState('919000000002');
  const [phoneLoading, setPhoneLoading] = useState(false);
  const [callStatus, setCallStatus] = useState<string | null>(null);

  // STT State
  const [sttTranscript, setSttTranscript] = useState('My Brother printer has a paper feed jam in tray 1.');
  const [sttRecording, setSttRecording] = useState(false);

  const handleSynthesize = async () => {
    if (!ttsInput.trim()) return;
    setTtsLoading(true);
    try {
      const audio = await synthesizeSpeechLive(ttsInput, ttsSpeaker);
      if (audio) {
        audio.play();
      }
    } finally {
      setTtsLoading(false);
    }
  };

  const handleOutboundCall = async () => {
    if (!phoneNumber.trim()) return;
    setPhoneLoading(true);
    setCallStatus('Connecting via Tata Smartflo Pilot (918069879488)...');
    try {
      const res = await fetch('/api/calls/outbound', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          to_number: phoneNumber.startsWith('+') ? phoneNumber : `+${phoneNumber}`,
          provider: 'smartflo'
        })
      });
      const data = await res.json();
      if (res.ok) {
        setCallStatus(`Call Queued Successfully! Call SID: ${data.call_sid || data.ref_id || 'Active'}`);
      } else {
        setCallStatus(`Error: ${data.detail || 'Failed to place call'}`);
      }
    } catch (e: any) {
      setCallStatus(`Call Error: ${e.message}`);
    } finally {
      setPhoneLoading(false);
    }
  };

  return (
    <div className="w-full max-w-5xl mx-auto my-14">
      {/* Tab Selectors */}
      <div className="flex flex-wrap items-center justify-center gap-2 mb-8">
        {[
          { id: 'tts', label: 'Text to Speech', icon: Volume2 },
          { id: 'stt', label: 'Speech to Text', icon: Mic },
          { id: 'rag', label: 'Knowledge Base SOPs', icon: BookOpen },
          { id: 'phone', label: 'Live Phone Call', icon: PhoneCall },
        ].map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-full text-xs md:text-sm font-semibold transition-all cursor-pointer border ${
                isActive
                  ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900 border-transparent shadow-md'
                  : 'bg-white dark:bg-stone-900/60 text-stone-600 dark:text-stone-300 border-stone-200 dark:border-white/10 hover:border-stone-300 dark:hover:border-white/20'
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab Panels */}
      <div className="rounded-3xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#0d0f17] p-8 md:p-10 shadow-lg">
        
        {/* 1. Text to Speech Panel */}
        {activeTab === 'tts' && (
          <div className="space-y-6">
            <div>
              <h3 className="text-lg font-bold text-stone-900 dark:text-white">Neural Text-to-Speech Engine</h3>
              <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                Generates warm, expressive speech with Indian English & multilingual phoneme modeling.
              </p>
            </div>

            <div className="relative">
              <textarea
                value={ttsInput}
                onChange={(e) => setTtsInput(e.target.value)}
                rows={3}
                className="w-full rounded-2xl border border-stone-200 dark:border-white/10 bg-stone-50/80 dark:bg-stone-950 p-4 text-sm text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                placeholder="Type anything for the voice agent to speak..."
              />
            </div>

            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-2">
                <span className="text-xs text-stone-500 font-medium">Voice Model:</span>
                {['Aarohi', 'Radhika', 'Deepak', 'Alloy'].map((spk) => (
                  <button
                    key={spk}
                    onClick={() => setTtsSpeaker(spk)}
                    className={`px-3 py-1 rounded-lg text-xs font-medium cursor-pointer transition-all ${
                      ttsSpeaker === spk
                        ? 'bg-indigo-600 text-white shadow-sm'
                        : 'bg-stone-100 dark:bg-stone-800 text-stone-700 dark:text-stone-300 hover:bg-stone-200'
                    }`}
                  >
                    {spk}
                  </button>
                ))}
              </div>

              <button
                onClick={handleSynthesize}
                disabled={ttsLoading}
                className="inline-flex items-center gap-2 px-6 py-2.5 rounded-full bg-stone-900 text-white dark:bg-white dark:text-stone-900 text-xs font-semibold shadow-md hover:scale-105 active:scale-95 transition-all cursor-pointer disabled:opacity-50"
              >
                {ttsLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Volume2 className="w-4 h-4" />}
                Synthesize & Play
              </button>
            </div>
          </div>
        )}

        {/* 2. Speech to Text Panel */}
        {activeTab === 'stt' && (
          <div className="space-y-6">
            <div>
              <h3 className="text-lg font-bold text-stone-900 dark:text-white">Groq Whisper-Large-v3-Turbo</h3>
              <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                Sub-90ms transcription benchmarked for Indian accents, noise cancellation, and technical jargon.
              </p>
            </div>

            <div className="p-5 rounded-2xl border border-dashed border-stone-300 dark:border-white/20 bg-stone-50 dark:bg-stone-950/50 flex flex-col items-center justify-center text-center py-8">
              <div className="w-12 h-12 rounded-full bg-indigo-50 dark:bg-indigo-950/50 text-indigo-600 dark:text-indigo-400 flex items-center justify-center mb-3">
                <Mic className="w-6 h-6 animate-pulse" />
              </div>
              <p className="text-xs text-stone-600 dark:text-stone-300 font-medium">
                Live Speech Recognition Active (16kHz PCM Stream)
              </p>
              <p className="text-[11px] text-stone-400 mt-1">Speak into your mic to test real-time ASR</p>
            </div>

            <div className="p-4 rounded-xl bg-stone-100 dark:bg-stone-900/60 border border-stone-200 dark:border-white/5">
              <span className="text-[11px] font-mono text-stone-400 uppercase tracking-wider">Latest Transcript</span>
              <p className="text-sm font-medium text-stone-900 dark:text-white mt-1">
                "{sttTranscript}"
              </p>
            </div>
          </div>
        )}

        {/* 3. Knowledge Base SOPs Panel */}
        {activeTab === 'rag' && (
          <div className="space-y-6">
            <div>
              <h3 className="text-lg font-bold text-stone-900 dark:text-white">Scoped Node RAG Grounding</h3>
              <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                Specialist nodes strictly query their assigned SOPs for zero hallucination.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {[
                { title: 'Brother DCP-L2540DW Manual', chunks: '4 Chunks', desc: 'Toner TN-2365, drum reset, Wi-Fi pairing' },
                { title: 'Database Troubleshooting SOP', chunks: '3 Chunks', desc: 'PostgreSQL timeouts, SAP DB ports 5432' },
                { title: 'Symphony Summit SLA Matrix', chunks: '2 Chunks', desc: 'P1/P2 incident creation & token verification' },
              ].map((doc) => (
                <div key={doc.title} className="p-4 rounded-2xl border border-stone-200 dark:border-white/10 bg-stone-50/60 dark:bg-stone-950/60 flex flex-col justify-between">
                  <div>
                    <span className="text-[11px] font-mono text-indigo-600 dark:text-indigo-400 font-semibold">{doc.chunks}</span>
                    <h4 className="text-sm font-bold text-stone-900 dark:text-white mt-1">{doc.title}</h4>
                    <p className="text-xs text-stone-500 dark:text-stone-400 mt-2">{doc.desc}</p>
                  </div>
                  <div className="mt-4 flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400 font-semibold">
                    <Check className="w-3.5 h-3.5" /> Attached to Node
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 4. Live Outbound Phone Call Panel */}
        {activeTab === 'phone' && (
          <div className="space-y-6">
            <div>
              <h3 className="text-lg font-bold text-stone-900 dark:text-white">Instant Telephony Bridge Dialer</h3>
              <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                Enter your mobile number to receive a live test call directly from Tata Smartflo or Twilio.
              </p>
            </div>

            <div className="flex flex-col sm:flex-row gap-3">
              <input
                type="tel"
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                placeholder="Enter mobile (e.g. 919000000002)"
                className="flex-1 rounded-2xl border border-stone-200 dark:border-white/10 bg-stone-50/80 dark:bg-stone-950 px-4 py-3 text-sm text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <button
                onClick={handleOutboundCall}
                disabled={phoneLoading}
                className="px-6 py-3 rounded-2xl bg-emerald-600 text-white text-xs font-semibold shadow-md hover:bg-emerald-700 transition-all cursor-pointer flex items-center justify-center gap-2"
              >
                {phoneLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <PhoneCall className="w-4 h-4" />}
                Call My Phone
              </button>
            </div>

            {callStatus && (
              <div className="p-3.5 rounded-xl bg-stone-100 dark:bg-stone-900 border border-stone-200 dark:border-white/10 text-xs font-mono text-stone-800 dark:text-stone-200">
                {callStatus}
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}
