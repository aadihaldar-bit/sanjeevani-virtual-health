'use client';

import React, { useState, useRef } from 'react';
import { Mic, MicOff, UploadCloud, Volume2, Play, Square, Loader2 } from 'lucide-react';
import { authFetch } from '@/lib/authFetch';

import { getApiBase } from '@/lib/api';

const LANGUAGES = [
  { code: 'en-IN', label: 'English (India)' },
  { code: 'hi-IN', label: 'Hindi' },
  { code: 'mr-IN', label: 'Marathi' },
  { code: 'ta-IN', label: 'Tamil' },
  { code: 'te-IN', label: 'Telugu' },
  { code: 'kn-IN', label: 'Kannada' },
  { code: 'gu-IN', label: 'Gujarati' },
  { code: 'bn-IN', label: 'Bengali' },
  { code: 'ml-IN', label: 'Malayalam' },
  { code: 'pa-IN', label: 'Punjabi' },
];

const TTS_VOICES = [
  { value: 'Aarohi', label: 'Aarohi (Female · en/hi/mr)' },
  { value: 'Radhika', label: 'Radhika (Female · en/hi)' },
  { value: 'Shweta', label: 'Shweta (Female · en/hi)' },
  { value: 'Nalini', label: 'Nalini (Female · ta/te)' },
  { value: 'Deepak', label: 'Deepak (Male · en/hi)' },
  { value: 'Gaurav', label: 'Gaurav (Male · en/hi)' },
  { value: 'Hemraj', label: 'Hemraj (Male · hi/mr)' },
];

// ─── STT Tab ─────────────────────────────────────────────────────────────────

function STTTab() {
  const [language, setLanguage] = useState('en-IN');
  const [mode, setMode] = useState<'live' | 'upload'>('live');
  const [recording, setRecording] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [latency, setLatency] = useState<number | null>(null);
  const [processing, setProcessing] = useState(false);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  async function sendChunk(blob: Blob) {
    setProcessing(true);
    try {
      const fd = new FormData();
      fd.append('audio_file', blob, 'chunk.wav');
      fd.append('language_code', language);
      const res = await authFetch(`${getApiBase()}/api/stt/transcribe`, { method: 'POST', body: fd });
      if (res.ok) {
        const data = await res.json();
        if (data.transcript) setTranscript(prev => prev ? `${prev} ${data.transcript}` : data.transcript);
        setLatency(data.latency_ms);
      }
    } catch (e) {
      console.error('STT error', e);
    } finally {
      setProcessing(false);
    }
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      mediaRef.current = mr;
      chunksRef.current = [];

      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        stream.getTracks().forEach(t => t.stop());
        await sendChunk(blob);
      };

      mr.start();
      setRecording(true);
      setTranscript('');
    } catch (e) {
      alert('Microphone access denied.');
    }
  }

  function stopRecording() {
    mediaRef.current?.stop();
    setRecording(false);
  }

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    await sendChunk(file);
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Config Panel */}
      <div className="w-72 border-r border-slate-200 dark:border-white/10 bg-white dark:bg-[#0d0f17] p-6 flex flex-col gap-5">
        <div>
          <label className="block text-xs font-semibold text-slate-500 dark:text-stone-400 uppercase tracking-wide mb-2">Model</label>
          <div className="px-3 py-2.5 border border-slate-200 dark:border-white/10 rounded-lg bg-slate-50 dark:bg-stone-800 text-sm text-slate-800 dark:text-stone-200 font-medium">
            Gnani Prisma V2.5
          </div>
          <p className="mt-1.5 text-[11px] text-slate-400 dark:text-stone-500 leading-relaxed">
            Transforms speech into accurate text across Indian languages
          </p>
        </div>

        <div>
          <label className="block text-xs font-semibold text-slate-500 dark:text-stone-400 uppercase tracking-wide mb-2">Input Language</label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="w-full px-3 py-2.5 border border-slate-200 dark:border-white/10 rounded-lg text-sm text-slate-800 dark:text-stone-200 bg-white dark:bg-stone-800 focus:outline-none focus:border-indigo-400"
          >
            {LANGUAGES.map(l => (
              <option key={l.code} value={l.code}>{l.label}</option>
            ))}
          </select>
        </div>

        {latency !== null && (
          <div className="px-3 py-2 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800/50 rounded-lg">
            <p className="text-xs text-emerald-600 font-medium">Last transcription: {latency}ms</p>
          </div>
        )}
      </div>

      {/* Canvas */}
      <div className="flex-1 flex flex-col items-center justify-center bg-[#fbfbfa] dark:bg-[#08090e] p-8">
        {/* Mode Toggle */}
        <div className="flex gap-1 mb-10 bg-white dark:bg-stone-900 border border-slate-200 dark:border-white/10 rounded-lg p-1">
          {(['live', 'upload'] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setTranscript(''); }}
              className={`px-5 py-1.5 rounded-md text-sm font-medium transition-all ${
                mode === m ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-500 dark:text-stone-400 hover:text-slate-700 dark:hover:text-white'
              }`}
            >
              {m === 'live' ? 'Speak Live' : 'Record / Upload'}
            </button>
          ))}
        </div>

        {mode === 'live' ? (
          <>
            <p className="text-sm text-slate-500 dark:text-stone-400 text-center mb-2">
              Speak in any language and see<br />the live transcription
            </p>

            {/* Mic button */}
            <button
              onClick={recording ? stopRecording : startRecording}
              className={`mt-6 w-24 h-24 rounded-full flex items-center justify-center transition-all ${
                recording
                  ? 'bg-indigo-600 text-white animate-mic-pulse'
                  : 'bg-white dark:bg-stone-800 border-2 border-slate-300 dark:border-stone-700 text-slate-500 dark:text-stone-300 hover:border-indigo-400 hover:text-indigo-500'
              }`}
            >
              {recording ? <MicOff className="w-10 h-10" /> : <Mic className="w-10 h-10" />}
            </button>

            <p className="mt-4 text-xs font-semibold text-slate-500 dark:text-stone-400 uppercase tracking-widest">
              {recording ? 'STOP' : 'START SPEAKING'}
            </p>
          </>
        ) : (
          <>
            <div className="w-20 h-20 rounded-full bg-white dark:bg-stone-800 border-2 border-slate-200 dark:border-stone-700 flex items-center justify-center mb-4">
              <UploadCloud className="w-10 h-10 text-slate-400" />
            </div>
            <p className="text-sm text-slate-500 dark:text-stone-400 text-center mb-4">
              Upload a WAV or MP3 file to transcribe
            </p>
            <input ref={fileRef} type="file" accept=".wav,.mp3,.webm,.ogg" onChange={handleFileUpload} className="hidden" />
            <button
              onClick={() => fileRef.current?.click()}
              className="px-5 py-2.5 bg-indigo-600 text-white text-sm font-semibold rounded-lg hover:bg-indigo-700 transition-colors"
            >
              Select Audio File
            </button>
          </>
        )}

        {/* Transcript */}
        {(transcript || processing) && (
          <div className="mt-8 w-full max-w-lg bg-white dark:bg-[#0d0f17] border border-slate-200 dark:border-white/10 rounded-xl p-5 shadow-sm">
            <p className="text-xs font-semibold text-slate-400 dark:text-stone-500 uppercase tracking-wide mb-2">Transcript</p>
            {processing && !transcript && (
              <div className="flex items-center gap-2 text-slate-400">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span className="text-sm">Processing...</span>
              </div>
            )}
            {transcript && (
              <p className="text-sm text-slate-800 dark:text-stone-200 leading-relaxed">{transcript}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── TTS Tab ─────────────────────────────────────────────────────────────────

function TTSTab() {
  const [text, setText] = useState('');
  const [voice, setVoice] = useState('Aarohi');
  const [language, setLanguage] = useState('en-IN');
  const [synthesizing, setSynthesizing] = useState(false);
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<AudioBuffer | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);

  async function handleSynthesize() {
    if (!text.trim()) return;
    setSynthesizing(true);
    try {
      const fd = new FormData();
      fd.append('text', text.trim());
      fd.append('voice', voice);
      fd.append('language_code', language);
      const res = await authFetch(`${getApiBase()}/api/tts/synthesize`, { method: 'POST', body: fd });
      if (!res.ok) throw new Error('TTS failed');
      const arrayBuffer = await res.arrayBuffer();
      const ctx = new AudioContext();
      ctxRef.current = ctx;
      const decoded = await ctx.decodeAudioData(arrayBuffer);
      audioRef.current = decoded;
      playAudio();
    } catch (e) {
      console.error('TTS error', e);
      alert('TTS synthesis failed. Check console.');
    } finally {
      setSynthesizing(false);
    }
  }

  function playAudio() {
    if (!audioRef.current || !ctxRef.current) return;
    const source = ctxRef.current.createBufferSource();
    source.buffer = audioRef.current;
    source.connect(ctxRef.current.destination);
    source.onended = () => setPlaying(false);
    source.start(0);
    sourceRef.current = source;
    setPlaying(true);
  }

  function stopAudio() {
    sourceRef.current?.stop();
    setPlaying(false);
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Config Panel */}
      <div className="w-72 border-r border-slate-200 dark:border-white/10 bg-white dark:bg-[#0d0f17] p-6 flex flex-col gap-5">
        <div>
          <label className="block text-xs font-semibold text-slate-500 dark:text-stone-400 uppercase tracking-wide mb-2">Voice</label>
          <select
            value={voice}
            onChange={(e) => setVoice(e.target.value)}
            className="w-full px-3 py-2.5 border border-slate-200 dark:border-white/10 rounded-lg text-sm text-slate-800 dark:text-stone-200 bg-white dark:bg-stone-800 focus:outline-none focus:border-indigo-400"
          >
            {TTS_VOICES.map(v => (
              <option key={v.value} value={v.value}>{v.label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-semibold text-slate-500 dark:text-stone-400 uppercase tracking-wide mb-2">Language</label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="w-full px-3 py-2.5 border border-slate-200 dark:border-white/10 rounded-lg text-sm text-slate-800 dark:text-stone-200 bg-white dark:bg-stone-800 focus:outline-none focus:border-indigo-400"
          >
            {LANGUAGES.map(l => (
              <option key={l.code} value={l.code}>{l.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Canvas */}
      <div className="flex-1 flex flex-col p-8 bg-[#fbfbfa] dark:bg-[#08090e]">
        <div className="max-w-xl w-full mx-auto flex-1 flex flex-col">
          <p className="text-xs text-slate-400 mb-1">Gnani Timbre V2.5</p>
          <h2 className="text-base font-semibold text-slate-800 dark:text-white mb-4">Enter text to synthesize</h2>

          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type or paste your text here..."
            className="w-full flex-1 min-h-[180px] px-4 py-3 border border-slate-200 dark:border-white/10 rounded-xl text-sm text-slate-800 dark:text-stone-100 placeholder-slate-400 dark:placeholder-stone-500 bg-white dark:bg-stone-900 focus:outline-none focus:border-indigo-400 resize-none"
          />

          <div className="flex gap-3 mt-4">
            <button
              onClick={handleSynthesize}
              disabled={!text.trim() || synthesizing}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 bg-indigo-600 text-white text-sm font-semibold rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {synthesizing ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Synthesizing...</>
              ) : (
                <><Volume2 className="w-4 h-4" /> Synthesize</>
              )}
            </button>

            {audioRef.current && (
              <button
                onClick={playing ? stopAudio : playAudio}
                className="px-4 py-2.5 border border-slate-300 dark:border-white/10 text-slate-600 dark:text-stone-300 hover:bg-slate-100 dark:hover:bg-stone-800 text-sm font-semibold rounded-lg transition-colors flex items-center gap-2"
              >
                {playing ? <Square className="w-4 h-4" /> : <Play className="w-4 h-4" />}
                {playing ? 'Stop' : 'Play Again'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main Export ─────────────────────────────────────────────────────────────

/** Which tool shows is chosen by the Tools tab bar, so there is no second switch here. */
export function SpeechPlayground({ initialMode = 'stt' }: { initialMode?: 'stt' | 'tts' }) {
  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {initialMode === 'stt' ? <STTTab /> : <TTSTab />}
    </div>
  );
}
