'use client';

import React from 'react';
import { HelpCircle, Plus, Search, Sparkles, X } from 'lucide-react';
import { STLLogo } from '@/components/STLLogo';
import { useShellStore } from '@/lib/shellStore';
import type { AgentLite } from '@/lib/useAgents';
import { SIDEBAR_GROUPS } from './nav';
import type { Section } from './nav';

interface Props {
  sections: Section[];
  activeId: string;
  agents: AgentLite[];
  canManage: boolean;
  onNavigate: (section: Section) => void;
  onHome: () => void;
  /** phone-sized screens: the sidebar slides over the page and can be closed */
  open: boolean;
  onClose: () => void;
}

const MAX_AGENTS_SHOWN = 5;

/**
 * The menu. Full width it shows labels, groups and a short list of your agents; collapsed it is a column of icons (it
 * collapses by itself while Architect is open, to give the chat room). Every icon keeps a tooltip and an accessible name.
 */
export function Sidebar({ sections, activeId, agents, canManage, onNavigate, onHome, open, onClose }: Props) {
  const { collapsed, architectOpen, toggleArchitect, setPalette, openNewAgent } = useShellStore();
  const compact = collapsed;                                   // on a phone the drawer is always full width
  const byId = (id: string) => sections.find((s) => s.id === id);

  const itemClass = (active: boolean) =>
    `group w-full flex items-center ${compact ? 'md:justify-center md:px-0' : ''} gap-3 h-9 px-3 rounded-lg text-[13px] font-medium transition-colors cursor-pointer ${
      active
        ? 'bg-stone-100 text-stone-900 dark:bg-white/10 dark:text-white'
        : 'text-stone-600 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-white/5 hover:text-stone-900 dark:hover:text-white'
    }`;
  const label = (text: string) => <span className={compact ? 'md:sr-only' : ''}>{text}</span>;

  return (
    <>
      {open && <div className="fixed inset-0 z-30 bg-stone-900/40 md:hidden" onClick={onClose} aria-hidden="true" />}
      <aside
        aria-label="Main navigation"
        className={`fixed md:static inset-y-0 left-0 z-40 flex-shrink-0 flex flex-col bg-white dark:bg-[#0c0e14] border-r border-stone-200 dark:border-white/10 transition-[transform,width] duration-200 w-64 ${
          compact ? 'md:w-[60px]' : 'md:w-64'
        } ${open ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}`}
      >
        <div className={`h-14 flex items-center justify-between ${compact ? 'md:justify-center md:px-0' : ''} px-4 border-b border-stone-200 dark:border-white/10`}>
          <button onClick={onHome} className="cursor-pointer" aria-label="Go to home">
            {compact ? <span className="hidden md:block text-[15px] font-black tracking-tighter text-stone-900 dark:text-white">STL</span> : null}
            <span className={compact ? 'md:hidden' : ''}><STLLogo /></span>
          </button>
          <button onClick={onClose} className="md:hidden p-1 text-stone-500" aria-label="Close menu"><X className="w-4 h-4" /></button>
        </div>

        <div className="px-3 pt-3 space-y-1">
          <button
            onClick={() => setPalette(true)}
            title="Search and create (Ctrl+K)"
            aria-label="Search and create"
            className={`w-full flex items-center ${compact ? 'md:justify-center md:px-0' : ''} gap-2.5 h-9 px-3 rounded-lg border border-stone-200 dark:border-white/10 text-[13px] text-stone-500 dark:text-stone-400 hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer`}
          >
            <Search className="w-4 h-4 flex-shrink-0" strokeWidth={1.75} />
            <span className={`flex-1 text-left ${compact ? 'md:hidden' : ''}`}>Search & create</span>
            <kbd className={`text-[10px] px-1.5 py-0.5 rounded border border-stone-200 dark:border-white/15 text-stone-400 ${compact ? 'md:hidden' : ''}`}>Ctrl K</kbd>
          </button>
          {canManage && (
            <button
              onClick={() => { toggleArchitect(); onClose(); }}
              aria-pressed={architectOpen}
              title="Build with Architect"
              className={`${itemClass(architectOpen)} !text-indigo-700 dark:!text-indigo-300`}
            >
              <Sparkles className="w-4 h-4 flex-shrink-0" strokeWidth={1.75} />
              {label('Architect')}
            </button>
          )}
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
          {SIDEBAR_GROUPS.map((group) => {
            const items = group.ids.map(byId).filter((s): s is Section => !!s);
            if (items.length === 0) return null;
            return (
              <div key={group.label || 'top'}>
                {group.label && (
                  <p className={`px-3 pb-1 text-[11px] font-medium text-stone-400 dark:text-stone-500 ${compact ? 'md:hidden' : ''}`}>{group.label}</p>
                )}
                {group.label && compact && <div className="hidden md:block mx-2 mb-2 border-t border-stone-100 dark:border-white/5" />}
                <div className="space-y-0.5">
                  {items.map((s) => {
                    const Icon = s.icon;
                    const active = s.id === activeId;
                    return (
                      <React.Fragment key={s.id}>
                        <div className="relative">
                          <button
                            onClick={() => onNavigate(s)}
                            aria-current={active ? 'page' : undefined}
                            title={s.label}
                            className={itemClass(active)}
                          >
                            <Icon className="w-4 h-4 flex-shrink-0" strokeWidth={active ? 2 : 1.75} />
                            {label(s.label)}
                          </button>
                          {s.id === 'agents' && canManage && !compact && (
                            <button
                              onClick={() => openNewAgent()}
                              title="New agent"
                              aria-label="New agent"
                              className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 rounded-md text-stone-400 hover:text-stone-900 dark:hover:text-white hover:bg-stone-200/60 dark:hover:bg-white/10 cursor-pointer"
                            >
                              <Plus className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </div>
                        {s.id === 'agents' && !compact && agents.length > 0 && (
                          <ul className="ml-4 pl-3 border-l border-stone-200 dark:border-white/10 space-y-0.5">
                            {agents.slice(0, MAX_AGENTS_SHOWN).map((a) => (
                              <li key={a.id}>
                                <a
                                  href={`/workflow?id=${a.id}`}
                                  title={`Open ${a.name} in the builder`}
                                  className="flex items-center gap-2 h-8 px-2 rounded-md text-[12.5px] text-stone-600 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-white/5 hover:text-stone-900 dark:hover:text-white"
                                >
                                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${a.is_active ? 'bg-emerald-500' : 'bg-stone-300 dark:bg-stone-600'}`} />
                                  <span className="truncate">{a.name}</span>
                                </a>
                              </li>
                            ))}
                            {agents.length > MAX_AGENTS_SHOWN && (
                              <li>
                                <button onClick={() => onNavigate(s)} className="h-8 px-2 text-[12px] text-stone-400 hover:text-stone-700 dark:hover:text-stone-200 cursor-pointer">
                                  {agents.length - MAX_AGENTS_SHOWN} more
                                </button>
                              </li>
                            )}
                          </ul>
                        )}
                      </React.Fragment>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>

        <div className="px-3 py-3 border-t border-stone-200 dark:border-white/10">
          <a href="/docs" target="_blank" rel="noreferrer" title="Documentation" className={itemClass(false)}>
            <HelpCircle className="w-4 h-4 flex-shrink-0" strokeWidth={1.75} />
            {label('Documentation')}
          </a>
        </div>
      </aside>
    </>
  );
}
