'use client';

import React from 'react';

export function EnterpriseTrustBar() {
  const brands = [
    'Tata Smartflo',
    'Twilio',
    'Meta Llama 3.3',
    'Groq',
    'OpenAI',
    'Deepgram',
    'Cartesia',
  ];

  return (
    <div className="w-full py-10 border-y border-stone-200/80 dark:border-white/5 bg-stone-50/50 dark:bg-stone-950/30">
      <div className="max-w-5xl mx-auto px-4 text-center">
        <p className="text-xs font-medium uppercase tracking-widest text-stone-400 dark:text-stone-500 mb-6">
          Built with a best-in-class voice &amp; telephony stack
        </p>
        <div className="flex flex-wrap items-center justify-center gap-8 md:gap-12 opacity-60 dark:opacity-40 grayscale hover:grayscale-0 transition-all">
          {brands.map((b) => (
            <span key={b} className="text-xs md:text-sm font-bold tracking-tight text-stone-700 dark:text-stone-300">
              {b}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
