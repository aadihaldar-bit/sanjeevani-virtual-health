'use client';

import React from 'react';
import { Mic, ArrowRight } from 'lucide-react';

/** Animated equalizer bars for the TTS card. */
function WaveBars() {
  const bars = [0.4, 0.75, 0.5, 1, 0.65, 0.9, 0.45];
  return (
    <div className="flex items-end gap-1.5 h-12">
      {bars.map((h, i) => (
        <span
          key={i}
          className="w-1.5 rounded-full bg-indigo-500/80 dark:bg-indigo-400/80 animate-pulse"
          style={{ height: `${h * 100}%`, animationDelay: `${i * 0.12}s`, animationDuration: '1.1s' }}
        />
      ))}
    </div>
  );
}

interface StackCard {
  tag: string;
  name: string;
  kind: string;
  blurb: string;
  ghost: string;
  visual: React.ReactNode;
}

const CARDS: StackCard[] = [
  {
    tag: 'STL ASR',
    name: 'Listen',
    kind: 'Speech-to-text',
    blurb: 'Streaming transcription tuned for Indian English, regional accents, and noisy telephony audio.',
    ghost: 'What…',
    visual: (
      <div className="w-16 h-16 rounded-full bg-indigo-500/10 dark:bg-indigo-400/10 flex items-center justify-center">
        <Mic className="w-7 h-7 text-indigo-600 dark:text-indigo-400" />
      </div>
    ),
  },
  {
    tag: 'STL TTS',
    name: 'Speak',
    kind: 'Text-to-speech',
    blurb: 'Natural, real-time voice synthesis across multiple languages, voices, and emotional styles.',
    ghost: '…make I’ll',
    visual: <WaveBars />,
  },
];

export function ProductStack() {
  return (
    <section className="w-full border-t border-stone-200/80 dark:border-white/5">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16">
        <div className="max-w-2xl mb-10">
          <p className="text-xs font-bold uppercase tracking-wider text-stone-400 mb-3">The stack</p>
          <h2 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-stone-900 dark:text-white">
            The full stack for <span className="text-indigo-600 dark:text-indigo-400">interactive intelligence</span>
          </h2>
          <p className="mt-3 text-sm text-stone-600 dark:text-stone-400 leading-relaxed">
            Built for low latency, long-context reasoning, and efficiency at scale — one platform for
            transcription, synthesis, and production voice agents.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {CARDS.map((card) => (
            <div
              key={card.tag}
              className="group rounded-3xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] overflow-hidden hover:shadow-xl transition-shadow"
            >
              {/* Visual panel */}
              <div className="relative h-56 bg-stone-50 dark:bg-stone-900/40 flex items-center justify-center border-b border-stone-100 dark:border-white/5">
                <span className="absolute left-6 top-1/2 -translate-y-1/2 text-2xl font-serif italic text-stone-300 dark:text-stone-700 select-none">
                  {card.ghost}
                </span>
                {card.visual}
              </div>

              {/* Caption */}
              <div className="p-6">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-stone-900 dark:text-white">{card.tag}.</span>
                  <span className="text-sm font-medium text-stone-600 dark:text-stone-300">{card.kind}</span>
                  <ArrowRight className="w-3.5 h-3.5 text-stone-400 group-hover:translate-x-0.5 transition-transform" />
                </div>
                <p className="mt-2 text-sm text-stone-600 dark:text-stone-400 leading-relaxed">{card.blurb}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
