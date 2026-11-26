'use client';

import React from 'react';
import { Mic, Volume2, Cpu, Code2, ShieldCheck, Wrench, Star, ArrowRight } from 'lucide-react';

function Node({
  icon,
  label,
  sub,
  active = false,
}: {
  icon: React.ReactNode;
  label: string;
  sub?: string;
  active?: boolean;
}) {
  return (
    <div
      className={`relative z-10 w-32 px-3 py-4 rounded-2xl border text-center bg-white dark:bg-[#121520] shadow-sm ${
        active
          ? 'border-indigo-500/70 ring-1 ring-indigo-500/20'
          : 'border-stone-200 dark:border-white/10'
      }`}
    >
      <div
        className={`mx-auto w-8 h-8 rounded-lg flex items-center justify-center mb-2 ${
          active
            ? 'bg-indigo-600 text-white'
            : 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-300'
        }`}
      >
        {icon}
      </div>
      <p className="text-xs font-bold text-stone-900 dark:text-white leading-tight">{label}</p>
      {sub && <p className="text-[10px] text-stone-500 dark:text-stone-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export function AgentArchitecture() {
  return (
    <section className="w-full border-t border-stone-200/80 dark:border-white/5">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16">
        <p className="text-xs font-bold uppercase tracking-wider text-stone-400 mb-3">Architecture</p>
        <h2 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-stone-900 dark:text-white mb-10">
          Voice agents as fast and accurate as the{' '}
          <span className="text-indigo-600 dark:text-indigo-400">models powering them</span>
        </h2>

        {/* Diagram */}
        <div className="relative rounded-3xl border border-stone-200/80 dark:border-white/10 bg-stone-50/50 dark:bg-[#0d0f17] px-4 sm:px-10 py-12 overflow-hidden">
          {/* faint flowing lines backdrop */}
          <svg
            className="absolute inset-0 w-full h-full text-indigo-500/10 dark:text-indigo-400/10"
            preserveAspectRatio="none"
            viewBox="0 0 800 300"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path d="M0 80 C200 20 600 260 800 120" />
            <path d="M0 150 C200 90 600 210 800 150" />
            <path d="M0 220 C200 280 600 40 800 180" />
          </svg>

          {/* Top row: STT -> Agent -> TTS */}
          <div className="relative flex items-center justify-center gap-4 sm:gap-10 flex-wrap">
            <Node icon={<Mic className="w-4 h-4" />} label="STL ASR" sub="Speech-to-text" />
            <ArrowRight className="w-5 h-5 text-stone-400 hidden sm:block" />
            <Node icon={<Star className="w-4 h-4" />} label="Agent Config" sub="Choose your LLM" active />
            <ArrowRight className="w-5 h-5 text-stone-400 hidden sm:block" />
            <Node icon={<Volume2 className="w-4 h-4" />} label="STL TTS" sub="Text-to-speech" />
          </div>

          {/* Connector down to tool row */}
          <div className="relative flex justify-center my-6">
            <div className="w-px h-8 bg-stone-300 dark:bg-white/15" />
          </div>

          {/* Bottom row: capabilities */}
          <div className="relative flex items-center justify-center gap-3 sm:gap-5 flex-wrap">
            <Node icon={<Cpu className="w-4 h-4" />} label="LLM" />
            <Node icon={<Code2 className="w-4 h-4" />} label="API" />
            <Node icon={<ShieldCheck className="w-4 h-4" />} label="Guardrails" />
            <Node icon={<Wrench className="w-4 h-4" />} label="Tools" />
          </div>
        </div>

        {/* Caption */}
        <div className="mt-6 flex items-center gap-2">
          <span className="font-bold text-stone-900 dark:text-white">Voice Agents.</span>
          <span className="text-sm text-stone-600 dark:text-stone-400">
            Build and ship enterprise voice agents on infrastructure grounded in your SOPs, tools, and telephony.
          </span>
        </div>
      </div>
    </section>
  );
}
