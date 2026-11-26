'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Building2, Check, ChevronDown, ExternalLink, LogOut, Menu, Moon, PanelLeft, Sparkles, Sun } from 'lucide-react';
import { useAuth } from '@/lib/useAuth';
import { useWorkspace } from '@/lib/useMe';
import { useDashboardStore } from '@/lib/store';
import { useShellStore } from '@/lib/shellStore';
import { useThemeStore } from '@/lib/themeStore';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { api, type Org } from '@/lib/adminApi';
import { getViewTenant, setViewTenant } from '@/lib/tenantView';

function useOutsideClose(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) close(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open, close]);
  return ref;
}

/** A system admin picks which company to look at; everyone else just sees their company's name. */
function CompanySwitcher() {
  const { me, isPlatform, workspaceName } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const ref = useOutsideClose(open, () => setOpen(false));
  const viewing = getViewTenant();

  useEffect(() => {
    if (!me?.is_super_admin) return;
    api<{ orgs: Org[] }>('/api/tenants').then((r) => setOrgs(r.orgs)).catch(() => setOrgs([]));
  }, [me?.is_super_admin]);

  if (!me?.is_super_admin) {
    return (
      <div className="hidden sm:flex items-center gap-2 h-8 px-3 rounded-lg border border-stone-200 dark:border-white/10 text-[13px] text-stone-700 dark:text-stone-300">
        <Building2 className="w-3.5 h-3.5 text-stone-400" strokeWidth={1.75} />
        <span className="truncate max-w-[12rem]">{isPlatform ? 'STL Digital' : workspaceName}</span>
      </div>
    );
  }

  const current = orgs.find((o) => String(o.id) === viewing);
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 h-8 pl-3 pr-2 rounded-lg border border-stone-200 dark:border-white/10 text-[13px] text-stone-700 dark:text-stone-200 hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer"
      >
        <Building2 className="w-3.5 h-3.5 text-stone-400" strokeWidth={1.75} />
        <span className="truncate max-w-[12rem]">{current ? current.name : 'All organizations'}</span>
        <ChevronDown className="w-3.5 h-3.5 text-stone-400" />
      </button>
      {open && (
        <div role="listbox" className="absolute right-0 mt-1 w-64 z-30 rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#12141c] shadow-lg py-1 max-h-80 overflow-y-auto">
          <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-stone-400">View data for</p>
          {[{ id: null as number | null, name: 'All organizations' }, ...orgs.map((o) => ({ id: o.id as number | null, name: o.name }))].map((o) => {
            const on = (o.id === null && !viewing) || String(o.id) === viewing;
            return (
              <button
                key={o.id ?? 'all'}
                role="option"
                aria-selected={on}
                onClick={() => { setOpen(false); if (!on) setViewTenant(o.id); }}
                className="w-full flex items-center justify-between gap-2 px-3 py-2 text-[13px] text-left text-stone-700 dark:text-stone-200 hover:bg-stone-100 dark:hover:bg-white/5 cursor-pointer"
              >
                <span className="truncate">{o.name}</span>
                {on && <Check className="w-3.5 h-3.5 text-indigo-600 flex-shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Credits() {
  const [credits, setCredits] = useState<{ used: number; total: number } | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await authFetch(`${getApiBase()}/api/gnani/credits`);
        if (res.ok && !cancelled) setCredits(await res.json());
      } catch { /* the chip just stays hidden */ }
    };
    load();
    const t = setInterval(load, 60000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);
  if (!credits) return null;
  const left = Math.max(credits.total - credits.used, 0);
  return (
    <div className="hidden lg:flex items-center gap-2 h-8 px-3 rounded-lg border border-stone-200 dark:border-white/10 text-[12px] text-stone-600 dark:text-stone-300" title="Speech and telephony minutes left">
      <span className="tabular-nums font-medium text-stone-900 dark:text-white">{left}</span>
      <span>of {credits.total} min left</span>
    </div>
  );
}

function AccountMenu({ onBackToLanding }: { onBackToLanding?: () => void }) {
  const { user, logOut } = useAuth();
  const { me } = useWorkspace();
  const { theme, toggleTheme } = useThemeStore();
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose(open, () => setOpen(false));
  const roleLabel: Record<string, string> = {
    super_admin: 'System administrator', tenant_admin: 'Administrator', manager: 'Manager', operator: 'Member', viewer: 'Viewer',
  };
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Account menu"
        aria-expanded={open}
        className="flex items-center rounded-full cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
      >
        {user?.photoURL ? (
          <img src={user.photoURL} alt="" className="w-8 h-8 rounded-full object-cover" />
        ) : (
          <span className="w-8 h-8 rounded-full bg-indigo-600 text-white flex items-center justify-center text-[11px] font-bold">{user?.initials || 'U'}</span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-64 z-30 rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#12141c] shadow-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-stone-100 dark:border-white/10">
            <p className="text-[13px] font-semibold text-stone-900 dark:text-white truncate">{user?.displayName || 'Signed in'}</p>
            <p className="text-[12px] text-stone-500 dark:text-stone-400 truncate">{user?.email}</p>
            {me && <p className="mt-1.5 inline-block text-[11px] px-2 py-0.5 rounded-full bg-stone-100 dark:bg-white/10 text-stone-600 dark:text-stone-300">{roleLabel[me.role] || me.role}</p>}
          </div>
          <div className="py-1">
            <button onClick={toggleTheme} className="w-full flex items-center gap-3 px-4 py-2 text-[13px] text-stone-700 dark:text-stone-200 hover:bg-stone-100 dark:hover:bg-white/5 cursor-pointer">
              {theme === 'dark' ? <Sun className="w-4 h-4" strokeWidth={1.75} /> : <Moon className="w-4 h-4" strokeWidth={1.75} />}
              {theme === 'dark' ? 'Light mode' : 'Dark mode'}
            </button>
            {onBackToLanding && (
              <button onClick={() => { setOpen(false); onBackToLanding(); }} className="w-full flex items-center gap-3 px-4 py-2 text-[13px] text-stone-700 dark:text-stone-200 hover:bg-stone-100 dark:hover:bg-white/5 cursor-pointer">
                <ExternalLink className="w-4 h-4" strokeWidth={1.75} /> Landing page & studio
              </button>
            )}
            <button
              onClick={() => { logOut(); if (onBackToLanding) onBackToLanding(); }}
              className="w-full flex items-center gap-3 px-4 py-2 text-[13px] text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-500/10 cursor-pointer"
            >
              <LogOut className="w-4 h-4" strokeWidth={1.75} /> Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

interface Props {
  title: string;
  onMenu: () => void;
  onBackToLanding?: () => void;
}

export function TopBar({ title, onMenu, onBackToLanding }: Props) {
  const { connected } = useDashboardStore();
  const { canManage } = useWorkspace();
  const { architectOpen, toggleArchitect, toggleSidebar, collapsed } = useShellStore();
  return (
    <header className="h-14 flex items-center justify-between gap-3 px-4 sm:px-5 border-b border-stone-200 dark:border-white/10 bg-white dark:bg-[#0c0e14] flex-shrink-0">
      <div className="flex items-center gap-2 min-w-0">
        <button onClick={onMenu} className="md:hidden p-1.5 -ml-1.5 rounded-lg text-stone-600 hover:bg-stone-100 dark:hover:bg-white/10 cursor-pointer" aria-label="Open menu">
          <Menu className="w-5 h-5" />
        </button>
        <button
          onClick={toggleSidebar}
          className="hidden md:flex p-1.5 rounded-lg text-stone-500 hover:text-stone-900 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-white/10 cursor-pointer"
          aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
          title={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
        >
          <PanelLeft className="w-[18px] h-[18px]" strokeWidth={1.75} />
        </button>
        <h1 className="text-[15px] font-semibold text-stone-900 dark:text-white truncate">{title}</h1>
      </div>
      <div className="flex items-center gap-2">
        <span
          className="hidden lg:flex items-center gap-1.5 text-[12px] text-stone-500 dark:text-stone-400 mr-1"
          title={connected ? 'Connected to the live call feed' : 'Not connected to the live call feed'}
        >
          <span className={`w-2 h-2 rounded-full ${connected ? 'bg-emerald-500' : 'bg-stone-300 dark:bg-stone-600'}`} />
          {connected ? 'Live' : 'Offline'}
        </span>
        <Credits />
        <CompanySwitcher />
        <a
          href="/docs"
          target="_blank"
          rel="noreferrer"
          className="hidden sm:inline-flex items-center h-8 px-3 rounded-lg border border-stone-200 dark:border-white/10 text-[13px] font-medium text-stone-700 dark:text-stone-200 hover:bg-stone-50 dark:hover:bg-white/5"
        >
          Docs
        </a>
        {canManage && (
          <button
            onClick={toggleArchitect}
            aria-pressed={architectOpen}
            className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border text-[13px] font-medium cursor-pointer transition-colors ${
              architectOpen
                ? 'border-indigo-300 bg-indigo-50 text-indigo-700 dark:border-indigo-400/40 dark:bg-indigo-500/15 dark:text-indigo-200'
                : 'border-stone-200 dark:border-white/10 text-stone-800 dark:text-stone-100 hover:bg-stone-50 dark:hover:bg-white/5'
            }`}
          >
            <Sparkles className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-300" strokeWidth={2} />
            <span className="hidden sm:inline">Build with Architect</span>
            <span className="sm:hidden">Architect</span>
          </button>
        )}
        <AccountMenu onBackToLanding={onBackToLanding} />
      </div>
    </header>
  );
}
