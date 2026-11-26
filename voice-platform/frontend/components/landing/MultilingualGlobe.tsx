'use client';

import React, { useRef, useEffect, useState } from 'react';
import { Play, Pause } from 'lucide-react';
import { synthesizeSpeechLive } from '@/lib/audioSamples';

interface Lang {
  id: string;
  label: string;
  code: string;
  voiceLang: string;
  sample: string;
}

const LANGUAGES: Lang[] = [
  { id: 'en', label: 'English (India)', code: 'en-IN', voiceLang: 'en-IN', sample: 'Hello! This is the STL Digital service desk. How can I help you today?' },
  { id: 'hi', label: 'Hindi', code: 'hi-IN', voiceLang: 'hi-IN', sample: 'नमस्ते! यह एसटीएल डिजिटल सेवा डेस्क है। मैं आपकी कैसे मदद कर सकता हूँ?' },
  { id: 'ta', label: 'Tamil', code: 'ta-IN', voiceLang: 'ta-IN', sample: 'வணக்கம்! இது எஸ்டிஎல் டிஜிட்டல் சேவை மையம். நான் எப்படி உதவ முடியும்?' },
  { id: 'te', label: 'Telugu', code: 'te-IN', voiceLang: 'te-IN', sample: 'నమస్తే! ఇది ఎస్‌టిఎల్ డిజిటల్ సేవా డెస్క్. నేను మీకు ఎలా సహాయం చేయగలను?' },
];

/* Rotating dotted globe on canvas (orthographic projection of a fibonacci sphere) */
function DotGlobe() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    const size = 360;
    canvas.width = size * DPR;
    canvas.height = size * DPR;
    ctx.scale(DPR, DPR);

    const N = 900;
    const R = size / 2 - 14;
    const cx = size / 2;
    const cy = size / 2;

    // fibonacci sphere points
    const pts: { x: number; y: number; z: number; hot: boolean }[] = [];
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2;
      const rad = Math.sqrt(1 - y * y);
      const theta = golden * i;
      pts.push({
        x: Math.cos(theta) * rad,
        y,
        z: Math.sin(theta) * rad,
        hot: (i * 2654435761) % 100 < 34, // stable ~34% "coverage" dots
      });
    }

    let angle = 0;
    let raf = 0;
    const dark = () =>
      document.documentElement.classList.contains('dark') ||
      (document.documentElement.getAttribute('data-theme') !== 'light' &&
        window.matchMedia('(prefers-color-scheme: dark)').matches);

    const draw = () => {
      ctx.clearRect(0, 0, size, size);
      const isDark = dark();
      angle += 0.0025;
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      for (const p of pts) {
        const rx = p.x * cos - p.z * sin;
        const rz = p.x * sin + p.z * cos;
        const sx = cx + rx * R;
        const sy = cy + p.y * R;
        const depth = (rz + 1) / 2; // 0 back .. 1 front
        const rr = 0.6 + depth * 1.7;
        let alpha = 0.15 + depth * 0.85;
        if (p.hot) {
          ctx.fillStyle = `rgba(99,102,241,${alpha})`; // indigo
        } else {
          ctx.fillStyle = isDark ? `rgba(160,160,170,${alpha * 0.5})` : `rgba(120,120,130,${alpha * 0.45})`;
        }
        ctx.beginPath();
        ctx.arc(sx, sy, rr, 0, Math.PI * 2);
        ctx.fill();
      }
      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={canvasRef} style={{ width: 360, height: 360 }} className="max-w-full" />;
}

export function MultilingualGlobe() {
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const stop = () => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current.currentTime = 0; }
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel();
    setPlayingId(null);
  };

  const toggle = async (lang: Lang) => {
    if (playingId === lang.id) return stop();
    stop();
    setPlayingId(lang.id);
    const audio = await synthesizeSpeechLive(lang.sample, { language: lang.voiceLang, onEnd: () => setPlayingId(null), onError: () => setPlayingId(null) } as any);
    if (audio) {
      audioRef.current = audio;
      audio.onended = () => setPlayingId(null);
      audio.onerror = () => setPlayingId(null);
      audio.play().catch(() => setPlayingId(null));
    } else setPlayingId(null);
  };

  useEffect(() => () => stop(), []);

  return (
    <section className="w-full border-t border-stone-200/80 dark:border-white/5">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16">
        <p className="text-xs font-bold uppercase tracking-wider text-stone-400 mb-3">Multilingual</p>
        <h2 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-stone-900 dark:text-white mb-10">
          An agent that sounds local, <span className="text-indigo-600 dark:text-indigo-400">wherever it picks up</span>
        </h2>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 items-center">
          {/* Globe */}
          <div className="flex items-center justify-center">
            <DotGlobe />
          </div>

          {/* Language list */}
          <div>
            <p className="text-xs font-bold uppercase tracking-wider text-stone-400 mb-4">Supported locales</p>
            <div className="divide-y divide-stone-200 dark:divide-white/10 border-y border-stone-200 dark:border-white/10">
              {LANGUAGES.map((lang) => (
                <div key={lang.id} className="flex items-center justify-between py-4">
                  <div className="flex items-center gap-3">
                    <span className="px-2 py-0.5 rounded-md bg-indigo-50 dark:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300 text-[10px] font-mono font-semibold border border-indigo-100 dark:border-indigo-900/40">
                      {lang.code}
                    </span>
                    <span className="text-sm font-medium text-stone-800 dark:text-stone-200">{lang.label}</span>
                  </div>
                  <button
                    onClick={() => toggle(lang)}
                    className="w-9 h-9 rounded-full border border-stone-200 dark:border-white/15 flex items-center justify-center text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 active:scale-95 transition-all cursor-pointer"
                  >
                    {playingId === lang.id ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
