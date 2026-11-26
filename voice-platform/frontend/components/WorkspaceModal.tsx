'use client';

import React from 'react';
import { X, Check, Building2, Radio, Phone, Cpu } from 'lucide-react';

export function WorkspaceModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-stone-900/40 backdrop-blur-xs flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-2xl shadow-xl w-full max-w-lg overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="p-5 border-b border-stone-200 dark:border-white/10 flex items-center justify-between bg-stone-50/70 dark:bg-stone-800/60">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-stone-900 text-white flex items-center justify-center font-bold text-xs">
              STL
            </div>
            <div>
              <h2 className="text-sm font-bold text-stone-900 dark:text-white">Workspace & Environment</h2>
              <p className="text-[11px] text-stone-500">Manage deployment credentials and telephony trunks</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-stone-400 hover:text-stone-700 hover:bg-stone-100 transition-colors cursor-pointer"
          >
            <X className="w-4 h-4 stroke-[1.5]" />
          </button>
        </div>

        {/* Content */}
        <div className="p-5 space-y-4 text-xs">
          {/* Active Workspace */}
          <div className="p-3.5 rounded-xl border-2 border-stone-900 dark:border-white/30 bg-stone-50/80 dark:bg-stone-800/80 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Building2 className="w-5 h-5 text-stone-800 dark:text-stone-200" />
              <div>
                <p className="font-bold text-stone-900 dark:text-white">STL Digital Enterprise</p>
                <p className="text-[10px] text-stone-500">Active Production Voice Mesh (Primary)</p>
              </div>
            </div>
            <div className="flex items-center gap-1 text-[10px] font-bold text-emerald-800 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
              <Check className="w-3 h-3" /> Active
            </div>
          </div>

          {/* Telephony Connection Details */}
          <div className="rounded-xl border border-stone-200 dark:border-white/10 p-3.5 space-y-2.5 bg-white dark:bg-stone-800/40">
            <h3 className="font-bold text-stone-800 dark:text-stone-200 text-[11px] uppercase tracking-wider">Connected Infrastructure</h3>
            
            <div className="flex items-center justify-between py-1 border-b border-stone-100 dark:border-white/5">
              <span className="text-stone-500 flex items-center gap-1.5">
                <Phone className="w-3.5 h-3.5 text-stone-400" /> Twilio SIP Trunk
              </span>
              <span className="font-mono text-stone-800 dark:text-stone-200 font-medium">+1 415-555-2671 / AC15f8a...</span>
            </div>

            <div className="flex items-center justify-between py-1 border-b border-stone-100 dark:border-white/5">
              <span className="text-stone-500 flex items-center gap-1.5">
                <Radio className="w-3.5 h-3.5 text-stone-400" /> Voice Synthesis
              </span>
              <span className="text-stone-800 dark:text-stone-200 font-medium">Gnani Timbre v2.5 (Prisma ASR)</span>
            </div>

            <div className="flex items-center justify-between py-1">
              <span className="text-stone-500 flex items-center gap-1.5">
                <Cpu className="w-3.5 h-3.5 text-stone-400" /> LLM Routing Engine
              </span>
              <span className="text-stone-800 dark:text-stone-200 font-medium">Groq LPU Llama 3.3 (70B)</span>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-stone-200 dark:border-white/10 bg-stone-50/70 dark:bg-stone-800/60 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-stone-900 dark:bg-white text-white dark:text-stone-900 font-semibold text-xs hover:bg-stone-800 dark:hover:bg-stone-100 transition-colors cursor-pointer"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
