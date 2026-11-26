'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Bot, CornerDownLeft, Plus, Search, Sparkles } from 'lucide-react';
import { useShellStore } from '@/lib/shellStore';
import type { TabId } from '@/lib/store';
import type { AgentLite } from '@/lib/useAgents';
import type { Section } from './nav';

interface Props {
  sections: Section[];
  agents: AgentLite[];
  canManage: boolean;
  onGo: (tab: TabId) => void;
}

interface Item { id: string; group: string; label: string; hint?: string; icon: React.ReactNode; run: () => void; }

/** "Search & create": jump to any page or agent, start something new, or hand a question to Architect. Ctrl+K opens it. */
export function CommandPalette({ sections, agents, canManage, onGo }: Props) {
  const { paletteOpen, setPalette, openNewAgent, askArchitect, openArchitect } = useShellStore();
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette(!useShellStore.getState().paletteOpen); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setPalette]);

  useEffect(() => { if (paletteOpen) { setQuery(''); setCursor(0); setTimeout(() => input.current?.focus(), 0); } }, [paletteOpen]);

  const close = () => setPalette(false);

  const items = useMemo<Item[]>(() => {
    const q = query.trim().toLowerCase();
    const out: Item[] = [];
    if (canManage) {
      out.push({ id: 'a-new', group: 'Create', label: 'Create a new agent', icon: <Plus className="w-4 h-4" />, run: () => openNewAgent() });
      out.push({ id: 'a-arch', group: 'Create', label: 'Build with Architect', hint: 'Describe it in your own words', icon: <Sparkles className="w-4 h-4" />, run: () => { close(); openArchitect(); } });
    }
    for (const s of sections) {
      for (const t of s.tabs) {
        const label = s.tabs.length > 1 ? `${s.label}: ${t.label}` : s.label;
        out.push({ id: `p-${t.id}`, group: 'Go to', label, icon: <ArrowRight className="w-4 h-4" />, run: () => { close(); onGo(t.id); } });
      }
    }
    for (const a of agents) {
      out.push({ id: `g-${a.id}`, group: 'Agents', label: a.name, hint: a.is_active ? 'Live' : undefined, icon: <Bot className="w-4 h-4" />, run: () => { window.location.href = `/workflow?id=${a.id}`; } });
    }
    const filtered = q ? out.filter((i) => i.label.toLowerCase().includes(q) || i.group.toLowerCase().includes(q)) : out.filter((i) => i.group !== 'Agents' || agents.length <= 4);
    if (q && canManage) {
      filtered.push({ id: 'ask', group: 'Ask', label: `Ask Architect: “${query.trim()}”`, icon: <Sparkles className="w-4 h-4" />, run: () => askArchitect(query.trim()) });
    }
    return filtered;
  }, [query, sections, agents, canManage]);        // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setCursor(0); }, [query]);
  useEffect(() => { list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }); }, [cursor]);

  if (!paletteOpen) return null;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { close(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, items.length - 1)); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
    if (e.key === 'Enter') { e.preventDefault(); items[cursor]?.run(); }
  };

  let lastGroup = '';
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[12vh] px-4 bg-stone-900/50 backdrop-blur-[2px]" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div role="dialog" aria-modal="true" aria-label="Search and create" onKeyDown={onKeyDown} className="w-full max-w-xl rounded-2xl bg-white dark:bg-[#12141c] border border-stone-200 dark:border-white/10 shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-4 h-12 border-b border-stone-100 dark:border-white/10">
          <Search className="w-4 h-4 text-stone-400" />
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages and agents, or describe what you want"
            aria-label="Search"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            className="flex-1 bg-transparent text-[14px] text-stone-900 dark:text-white placeholder:text-stone-400 focus:outline-none"
          />
          <kbd className="text-[10px] px-1.5 py-0.5 rounded border border-stone-200 dark:border-white/15 text-stone-400">Esc</kbd>
        </div>
        <ul id="palette-list" ref={list} role="listbox" className="max-h-[50vh] overflow-y-auto py-2">
          {items.length === 0 && <li className="px-4 py-6 text-center text-[13px] text-stone-500">Nothing matches. Try another word.</li>}
          {items.map((item, i) => {
            const header = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return (
              <React.Fragment key={item.id}>
                {header && <li role="presentation" className="px-4 pt-2 pb-1 text-[11px] font-medium text-stone-400">{header}</li>}
                <li role="option" aria-selected={i === cursor}>
                  <button
                    onMouseMove={() => setCursor(i)}
                    onClick={item.run}
                    className={`w-full flex items-center gap-3 px-4 h-10 text-left text-[13px] cursor-pointer ${i === cursor ? 'bg-stone-100 dark:bg-white/10 text-stone-900 dark:text-white' : 'text-stone-700 dark:text-stone-300'}`}
                  >
                    <span className="text-stone-400">{item.icon}</span>
                    <span className="flex-1 truncate">{item.label}</span>
                    {item.hint && <span className="text-[11px] text-stone-400">{item.hint}</span>}
                    {i === cursor && <CornerDownLeft className="w-3.5 h-3.5 text-stone-400" />}
                  </button>
                </li>
              </React.Fragment>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
