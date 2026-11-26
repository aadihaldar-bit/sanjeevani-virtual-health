'use client';

import React from 'react';
import { Lock, Server, KeyRound, ScrollText, Cloud, Users } from 'lucide-react';

const ITEMS = [
  {
    icon: Lock,
    title: 'Encrypted in transit',
    body: 'Call audio and API traffic are served over TLS; media streams stay inside the session.',
  },
  {
    icon: Cloud,
    title: 'Your own cloud',
    body: 'Runs on your GCP project and compute — data stays within infrastructure you control.',
  },
  {
    icon: KeyRound,
    title: 'Scoped API access',
    body: 'Per-client API keys and tokens gate every programmatic call to the platform.',
  },
  {
    icon: Users,
    title: 'Authenticated console',
    body: 'Operator access to the console is gated behind Google sign-in, not open to the public.',
  },
  {
    icon: ScrollText,
    title: 'Audit trail',
    body: 'Every call, transcript, and auto-created ticket is logged and retained for review.',
  },
  {
    icon: Server,
    title: 'Cloud or on-prem',
    body: 'Deploy the same stack in the cloud or inside your own network for data-residency needs.',
  },
];

export function SecuritySection() {
  return (
    <section className="w-full border-t border-stone-200/80 dark:border-white/5">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16">
        <p className="text-xs font-bold uppercase tracking-wider text-stone-400 mb-3">Enterprise-grade</p>
        <h2 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-stone-900 dark:text-white mb-10 max-w-xl">
          Security that travels, <span className="text-indigo-600 dark:text-indigo-400">from cloud to your network</span>
        </h2>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <div
                key={item.title}
                className="p-6 rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] hover:shadow-md transition-shadow"
              >
                <div className="w-9 h-9 rounded-lg bg-indigo-50 dark:bg-indigo-950/50 flex items-center justify-center mb-4">
                  <Icon className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                </div>
                <h3 className="text-sm font-bold text-stone-900 dark:text-white">{item.title}</h3>
                <p className="mt-1.5 text-xs text-stone-600 dark:text-stone-400 leading-relaxed">{item.body}</p>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
