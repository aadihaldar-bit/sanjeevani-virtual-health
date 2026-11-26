'use client';

import React from 'react';
import Link from 'next/link';
import { STLLogo } from '@/components/STLLogo';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Search, ExternalLink, Activity, ArrowUpRight, Terminal } from 'lucide-react';

interface DocsNavbarProps {
  searchQuery: string;
  onSearchChange: (q: string) => void;
  onOpenSearch?: () => void;
}

export function DocsNavbar({ searchQuery, onSearchChange }: DocsNavbarProps) {
  return (
    <header className="sticky top-0 z-40 w-full border-b border-stone-200 dark:border-white/10 bg-white/95 dark:bg-[#0a0c10]/95 backdrop-blur-md transition-colors">
      <div className="max-w-[1720px] mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
        {/* Left: Logo & API Badge */}
        <div className="flex items-center gap-4 flex-shrink-0">
          <Link href="/" className="flex items-center gap-2 hover:opacity-90 transition-opacity">
            <STLLogo />
          </Link>
          <div className="h-4 w-[1px] bg-stone-300 dark:bg-white/15 hidden sm:block" />
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-stone-900 dark:text-white tracking-tight flex items-center gap-1.5">
              <Terminal className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
              API Reference
            </span>
            <span className="px-1.5 py-0.5 text-[10px] font-mono font-medium rounded-md bg-stone-100 dark:bg-white/10 text-stone-600 dark:text-stone-300 border border-stone-200 dark:border-white/10">
              v2.5.0
            </span>
          </div>
        </div>

        {/* Center: Search input */}
        <div className="flex-1 max-w-md hidden md:block">
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 dark:text-stone-500 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder="Search endpoints, methods, parameters..."
              className="w-full h-8 pl-8 pr-12 rounded-lg text-xs bg-stone-100 dark:bg-stone-900/90 text-stone-900 dark:text-stone-100 placeholder-stone-400 dark:placeholder-stone-500 border border-stone-200 dark:border-white/10 focus:outline-none focus:border-indigo-500 dark:focus:border-indigo-400 transition-all font-sans"
            />
            <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-0.5 pointer-events-none">
              <kbd className="px-1.5 py-0.5 text-[9px] font-mono rounded bg-stone-200 dark:bg-white/10 text-stone-500 dark:text-stone-400 border border-stone-300 dark:border-white/10">
                ⌘K
              </kbd>
            </div>
          </div>
        </div>

        {/* Right: Telemetry pill, Theme toggle, Console link */}
        <div className="flex items-center gap-3">
          {/* Status SLA Indicator */}
          <div className="hidden lg:flex items-center gap-2 px-2.5 py-1 rounded-full text-[11px] font-medium bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800/40">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span>Operational · Sub-100ms SLA</span>
          </div>

          <ThemeToggle />

          <Link
            href="/"
            className="hidden sm:inline-flex items-center gap-1 text-xs font-medium text-stone-600 dark:text-stone-300 hover:text-stone-900 dark:hover:text-white px-2.5 py-1.5 transition-colors"
          >
            <span>Overview</span>
          </Link>

          <Link
            href="/?tab=dashboard"
            className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-700 text-white shadow-sm transition-all hover:shadow hover:-translate-y-0.5"
          >
            <span>Console</span>
            <ArrowUpRight className="w-3.5 h-3.5" />
          </Link>
        </div>
      </div>
    </header>
  );
}
