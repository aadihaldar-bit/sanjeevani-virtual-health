'use client';

import React from 'react';
import { STLLogo } from '@/components/STLLogo';

export function LandingFooter() {
  return (
    <footer className="w-full border-t border-stone-200 dark:border-white/10 bg-white dark:bg-[#08090e] py-14">
      <div className="max-w-6xl mx-auto px-4 sm:px-6">
        
        {/* Top Grid (Matching Image 5) */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-8 pb-12 border-b border-stone-100 dark:border-white/5">
          
          {/* Col 1: Brand & Language */}
          <div className="col-span-2 md:col-span-1 space-y-3">
            <STLLogo />
            <p className="text-xs text-stone-500 dark:text-stone-400 leading-relaxed">
              Enterprise Conversational Intelligence &amp; Carrier Telephony Platform by STL Digital.
            </p>
            <div className="pt-2">
              <select className="bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-lg px-2.5 py-1 text-xs text-stone-700 dark:text-stone-300 focus:outline-none cursor-pointer">
                <option>🇺🇸 English (Global)</option>
                <option>🇮🇳 Hindi (hi-IN)</option>
                <option>🇮🇳 Tamil (ta-IN)</option>
                <option>🇮🇳 Telugu (te-IN)</option>
              </select>
            </div>
          </div>

          {/* Col 2: STL Voice Studio */}
          <div>
            <h5 className="text-xs font-bold uppercase tracking-wider text-stone-900 dark:text-white mb-3">STL Voice Studio</h5>
            <ul className="space-y-2 text-xs text-stone-500 dark:text-stone-400">
              <li>Text to Speech Synthesis</li>
              <li>Speech to Text Whisper ASR</li>
              <li>Neural Voice Tuning</li>
              <li>Audio Chimes &amp; Prompts</li>
              <li>Multi-Language Phonemes</li>
            </ul>
          </div>

          {/* Col 3: STL Voice Agents */}
          <div>
            <h5 className="text-xs font-bold uppercase tracking-wider text-stone-900 dark:text-white mb-3">STL Voice Agents</h5>
            <ul className="space-y-2 text-xs text-stone-500 dark:text-stone-400">
              <li>Conversational Multi-Agent Graphs</li>
              <li>IT Service Desk Reception</li>
              <li>Brother Printer SOP Specialist</li>
              <li>Database &amp; SAP Recovery</li>
              <li>Symphony Summit SLA Ticketing</li>
            </ul>
          </div>

          {/* Col 4: STL Voice API */}
          <div>
            <h5 className="text-xs font-bold uppercase tracking-wider text-stone-900 dark:text-white mb-3">STL Voice API</h5>
            <ul className="space-y-2 text-xs text-stone-500 dark:text-stone-400">
              <li><a href="/docs" className="hover:text-stone-900 dark:hover:text-white">API Reference Docs</a></li>
              <li>Bidirectional WebSocket Audio</li>
              <li>Tata Smartflo CDR Webhook</li>
              <li>Twilio SIP Integration</li>
              <li>Python &amp; REST SDKs</li>
            </ul>
          </div>

        </div>

        {/* Bottom Row: Legal & Copyright */}
        <div className="pt-8 flex flex-col sm:flex-row items-center justify-between text-[11px] text-stone-400 gap-4">
          <div className="flex items-center gap-6">
            <span>About STL Digital</span>
            <span>Security &amp; Privacy</span>
            <span>SLA Policies</span>
            <span>System Telemetry</span>
          </div>
          <p>© 2026 STL Digital Inc. All rights reserved.</p>
        </div>

      </div>
    </footer>
  );
}
