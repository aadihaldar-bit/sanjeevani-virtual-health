'use client';

import React, { useEffect, useState } from 'react';
import {
  LayoutGrid, Bot, Map, Phone, BarChart3, ArrowUpRight,
  Book, Stethoscope, HelpCircle, Mic, Volume2, ChevronDown, CheckCircle2, FileText, FlaskConical, LogOut, KeyRound, HeartPulse, ShieldCheck, PhoneCall, Pill
} from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import type { TabId } from '@/lib/store';
import { domainTabsForWorkflow, isDomainTab } from '@/lib/workflowTabs';
import { STLLogo } from '@/components/STLLogo';
import { ThemeToggle } from '@/components/ThemeToggle';
import { WorkspaceModal } from '@/components/WorkspaceModal';
import { authFetch } from '@/lib/authFetch';
import { useAuth } from '@/lib/useAuth';
import { useWorkspace } from '@/lib/useMe';

import { getApiBase } from '@/lib/api';

interface NavItem { id: TabId; label: string; icon: React.ReactNode; }

const PLAYGROUND_NAV: NavItem[] = [
  { id: 'simulator', label: 'Agent Simulator', icon: <FlaskConical className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'speech-stt', label: 'Speech to Text', icon: <Mic className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'speech-tts', label: 'Text to Speech', icon: <Volume2 className="w-4 h-4 stroke-[1.5]" /> },
];

const BUILD_NAV: NavItem[] = [
  { id: 'home', label: 'Dashboard', icon: <LayoutGrid className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'agents', label: 'Agents', icon: <Bot className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'api-keys', label: 'API Keys', icon: <KeyRound className="w-4 h-4 stroke-[1.5]" /> },
];

// Common Monitor tabs — always visible (they mean the same thing for every workflow).
const MONITOR_COMMON_NAV: NavItem[] = [
  { id: 'live', label: 'Live Calls', icon: <Phone className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'completed', label: 'Completed Calls', icon: <CheckCircle2 className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'analytics', label: 'Analytics', icon: <BarChart3 className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'knowledge', label: 'Knowledge Base', icon: <Book className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'diagnostics', label: 'Diagnostics', icon: <Stethoscope className="w-4 h-4 stroke-[1.5]" /> },
];

// Workflow-specific tabs — shown only when their workflow is the one being viewed.
const DOMAIN_NAV: NavItem[] = [
  { id: 'tickets', label: 'Symphony Incidents', icon: <FileText className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'escalations', label: 'Escalations', icon: <ArrowUpRight className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'health', label: 'Health System', icon: <HeartPulse className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'sanjeevani-followups', label: 'Follow-up calls', icon: <PhoneCall className="w-4 h-4 stroke-[1.5]" /> },
  { id: 'sanjeevani-prescriptions', label: 'Prescriptions', icon: <Pill className="w-4 h-4 stroke-[1.5]" /> },
];

interface WorkflowLite { id: number; name: string; is_active: boolean; }

function NavSection({ title, items, activeTab, onNav }: {
  title: string;
  items: NavItem[];
  activeTab: TabId;
  onNav: (id: TabId) => void;
}) {
  return (
    <div className="mb-2">
      <p className="px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-stone-400 dark:text-stone-500">
        {title}
      </p>
      {items.map((item) => (
        <button
          key={item.id}
          onClick={() => onNav(item.id)}
          className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium transition-all text-left cursor-pointer ${
            activeTab === item.id
              ? 'bg-stone-200/70 dark:bg-white/10 text-stone-900 dark:text-white font-semibold shadow-xs'
              : 'text-stone-600 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-white/5 hover:text-stone-900 dark:hover:text-white'
          }`}
        >
          <span className={activeTab === item.id ? 'text-stone-900 dark:text-white' : 'text-stone-400'}>
            {item.icon}
          </span>
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function GnaniSidebar({ onBackToLanding }: { onBackToLanding?: () => void }) {
  const { user, logOut } = useAuth();
  const { me, canManage, workspaceName } = useWorkspace();
  // Administration is shown only to people who can administer something (platform or organization administrators).
  const adminNav: NavItem[] = me && (me.is_super_admin || me.role === 'tenant_admin')
    ? [{ id: 'admin', label: 'Organizations & access', icon: <ShieldCheck className="w-4 h-4 stroke-[1.5]" /> }] : [];
  const { selectedTab, setSelectedTab, connected, viewWorkflowId, setViewWorkflow } = useDashboardStore();
  const [credits, setCredits] = useState<{ used: number; total: number } | null>(null);
  const [workspaceModalOpen, setWorkspaceModalOpen] = useState(false);
  const [creditsModalOpen, setCreditsModalOpen] = useState(false);
  const [workflows, setWorkflows] = useState<WorkflowLite[]>([]);
  const [wfMenuOpen, setWfMenuOpen] = useState(false);

  // Load workflows so we know which one is live (is_active) and can offer the view switcher.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch(`${getApiBase()}/api/agents/`);
        if (!res.ok) return;
        const data = await res.json();
        const list: WorkflowLite[] = (Array.isArray(data) ? data : data.agents || [])
          .map((w: any) => ({ id: w.id, name: w.name, is_active: !!w.is_active }));
        if (!cancelled) setWorkflows(list);
      } catch { /* non-fatal: fall back to all-common nav */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const activeWorkflow = workflows.find((w) => w.is_active) || null;
  const viewedWorkflow =
    workflows.find((w) => w.id === viewWorkflowId) || activeWorkflow || null;
  const domainTabs = domainTabsForWorkflow(viewedWorkflow?.name);
  const visibleDomainNav = DOMAIN_NAV.filter((it) => domainTabs.includes(it.id));

  // If the open tab belongs to a workflow we're no longer viewing, fall back to a common tab.
  useEffect(() => {
    if (isDomainTab(selectedTab) && !domainTabs.includes(selectedTab)) {
      setSelectedTab('completed');
    }
  }, [viewedWorkflow?.id, domainTabs, selectedTab, setSelectedTab]);

  const byId = (id: TabId) => MONITOR_COMMON_NAV.find((i) => i.id === id)!;
  const monitorItems: NavItem[] = [
    byId('live'), byId('completed'),
    ...visibleDomainNav,
    byId('analytics'), byId('knowledge'),
    byId('diagnostics'),
  ];
  // Everyone has the tools; API keys are only for those who manage them.
  const buildNav = canManage ? BUILD_NAV : BUILD_NAV.filter((i) => i.id !== 'api-keys');

  // Live credit fetch every 60s
  useEffect(() => {
    async function fetchCredits() {
      try {
        const res = await authFetch(`${getApiBase()}/api/gnani/credits`);
        if (res.ok) {
          const data = await res.json();
          setCredits(data);
        } else {
          setCredits({ used: 20, total: 1000 });
        }
      } catch {
        setCredits({ used: 20, total: 1000 });
      }
    }
    fetchCredits();
    const interval = setInterval(fetchCredits, 60000);
    return () => clearInterval(interval);
  }, []);

  const creditPct = credits ? Math.min((credits.used / credits.total) * 100, 100) : 2;
  const r = 10;
  const circumference = 2 * Math.PI * r;
  const dashOffset = circumference - (creditPct / 100) * circumference;

  return (
    <>
      <div className="w-60 min-w-[15rem] h-full flex flex-col bg-[#fdfdfc] dark:bg-[#0d0f17] border-r border-stone-200/90 dark:border-white/10 flex-shrink-0 transition-colors duration-300">
        {/* Logo */}
        <div 
          onClick={() => setSelectedTab('home')}
          className="h-14 flex items-center px-4 border-b border-stone-200/80 dark:border-white/10 justify-between cursor-pointer hover:bg-stone-50 dark:hover:bg-white/5 transition-colors"
        >
          <STLLogo />
          <div className="flex items-center gap-1.5" title={connected ? 'Telephony Engine Connected' : 'Engine Disconnected'}>
            <div className={`w-2 h-2 rounded-full ${connected ? 'bg-emerald-500 ring-2 ring-emerald-500/20' : 'bg-rose-400'}`} />
          </div>
        </div>

        {/* Workspace pill */}
        <div className="px-3 py-2.5 border-b border-stone-200/80 dark:border-white/10 bg-stone-50/60 dark:bg-stone-950/40">
          <button 
            onClick={() => setWorkspaceModalOpen(true)}
            className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 shadow-xs text-stone-800 dark:text-stone-200 text-xs font-medium hover:bg-stone-50 dark:hover:bg-stone-800 transition-colors cursor-pointer"
          >
            <div className="flex items-center gap-2">
              <span className="w-4 h-4 rounded bg-stone-900 dark:bg-white dark:text-stone-900 flex items-center justify-center text-[9px] font-bold text-white">{workspaceName.charAt(0).toUpperCase()}</span>
              <span className="truncate">{workspaceName}</span>
            </div>
            <ChevronDown className="w-3.5 h-3.5 text-stone-400" />
          </button>
        </div>

      {/* Nav */}
      <div className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
        {onBackToLanding && (
          <div className="mb-3 px-1">
            <button
              onClick={onBackToLanding}
              type="button"
              className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs font-semibold text-indigo-600 dark:text-indigo-400 bg-indigo-50/80 dark:bg-indigo-950/50 border border-indigo-200/70 dark:border-indigo-800/50 hover:bg-indigo-100 dark:hover:bg-indigo-900/60 transition-all cursor-pointer shadow-2xs"
              title="Return to Public Landing Page & Studio"
            >
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />
                <span>Landing Page & Studio</span>
              </div>
              <span className="text-[10px] font-mono opacity-70">↗</span>
            </button>
          </div>
        )}

        <NavSection title="Playground" items={PLAYGROUND_NAV} activeTab={selectedTab} onNav={setSelectedTab} />
        <NavSection title="Build" items={buildNav} activeTab={selectedTab} onNav={setSelectedTab} />
        {adminNav.length > 0 && <NavSection title="Administration" items={adminNav} activeTab={selectedTab} onNav={setSelectedTab} />}

        {/* Workflow view switcher — changes which domain tabs show; does NOT change the live bot. */}
        {workflows.length > 0 && (
          <div className="mb-2 px-1">
            <p className="px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-stone-400 dark:text-stone-500">
              Viewing workflow
            </p>
            <div className="relative">
              <button
                onClick={() => setWfMenuOpen((o) => !o)}
                className="w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 shadow-xs text-stone-800 dark:text-stone-200 text-xs font-medium hover:bg-stone-50 dark:hover:bg-stone-800 transition-colors cursor-pointer"
                title="Choose which workflow's data to view. This does not change which workflow answers live calls."
              >
                <span className="flex items-center gap-1.5 min-w-0">
                  {viewedWorkflow && viewedWorkflow.id === activeWorkflow?.id && (
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 flex-shrink-0" title="Live on the phones" />
                  )}
                  <span className="truncate">{viewedWorkflow?.name || 'All workflows'}</span>
                </span>
                <ChevronDown className="w-3.5 h-3.5 text-stone-400 flex-shrink-0" />
              </button>
              {wfMenuOpen && (
                <div className="absolute z-20 mt-1 w-full rounded-lg bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 shadow-lg py-1 max-h-72 overflow-y-auto">
                  {workflows.map((w) => (
                    <button
                      key={w.id}
                      onClick={() => { setViewWorkflow(w.id, w.name); setWfMenuOpen(false); }}
                      className={`w-full flex items-center justify-between gap-2 px-2.5 py-1.5 text-xs text-left hover:bg-stone-100 dark:hover:bg-white/5 cursor-pointer ${
                        w.id === viewedWorkflow?.id ? 'text-stone-900 dark:text-white font-semibold' : 'text-stone-600 dark:text-stone-300'
                      }`}
                    >
                      <span className="truncate">{w.name}</span>
                      {w.is_active && (
                        <span className="flex items-center gap-1 text-[9px] font-semibold text-emerald-600 dark:text-emerald-400 flex-shrink-0">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Live
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        <NavSection title="Monitor" items={monitorItems} activeTab={selectedTab} onNav={setSelectedTab} />

        {/* Workflow Canvas — external link */}
        <div className="mb-2">
          <a
            href="/workflow"
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium text-stone-600 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-white/5 hover:text-stone-900 dark:hover:text-white transition-all"
          >
            <Map className="w-4 h-4 text-stone-400 stroke-[1.5]" />
            Workflow Canvas
          </a>
        </div>

        {/* Quick External Links */}
        <div className="pt-2 border-t border-stone-200/80 dark:border-white/5 space-y-0.5">
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="w-full flex items-center gap-2.5 px-3 py-1.5 rounded-lg text-xs text-stone-500 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-white/5 hover:text-stone-800 dark:hover:text-stone-200 transition-all"
          >
            <HelpCircle className="w-3.5 h-3.5" />
            Documentation
          </a>
        </div>
      </div>

      {/* Footer / User Profile & Theme Toggle */}
      <div className="p-3 border-t border-stone-200/80 dark:border-white/10 bg-stone-50/60 dark:bg-stone-950/40 space-y-2">
        
        {/* Theme switcher pill */}
        <div className="flex items-center justify-between px-1">
          <span className="text-[11px] font-medium text-stone-500 dark:text-stone-400">Theme</span>
          <ThemeToggle />
        </div>

        {/* Credits Pill */}
        <button
          onClick={() => setCreditsModalOpen(true)}
          className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-stone-200/80 dark:border-white/10 bg-white dark:bg-stone-900 text-left hover:bg-stone-50 dark:hover:bg-stone-800 transition-colors cursor-pointer"
        >
          <svg className="w-4 h-4 flex-shrink-0" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r={r} fill="none" stroke="#E7E5E4" strokeWidth="2.5" />
            <circle
              cx="12" cy="12" r={r}
              fill="none"
              stroke="#44403C"
              strokeWidth="2.5"
              strokeDasharray={circumference}
              strokeDashoffset={dashOffset}
              strokeLinecap="round"
              transform="rotate(-90 12 12)"
            />
          </svg>
          <span className="text-xs text-stone-600 dark:text-stone-300 font-medium truncate">
            {credits ? `${credits.used} / ${credits.total} Left` : 'Loading...'}
          </span>
        </button>

        {/* User Profile & Logout */}
        <div className="flex items-center justify-between px-2 py-1">
          <div className="flex items-center gap-2.5 min-w-0">
            {user?.photoURL ? (
              <img
                src={user.photoURL}
                alt={user.displayName}
                className="w-7 h-7 rounded-full object-cover flex-shrink-0"
              />
            ) : (
              <div className="w-7 h-7 rounded-full bg-indigo-600 text-white flex items-center justify-center text-[10px] font-bold flex-shrink-0">
                {user?.initials || 'U'}
              </div>
            )}
            <div className="min-w-0">
              <p className="text-xs font-semibold text-stone-900 dark:text-white truncate">
                {user?.displayName || 'Enterprise User'}
              </p>
              <p className="text-[10px] text-stone-500 dark:text-stone-400 truncate">
                {user?.email || 'Google Authenticated'}
              </p>
            </div>
          </div>
          <button
            onClick={() => {
              logOut();
              if (onBackToLanding) onBackToLanding();
            }}
            type="button"
            title="Log out and return to landing page"
            className="w-7 h-7 rounded-lg text-stone-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 flex items-center justify-center transition-all cursor-pointer flex-shrink-0 ml-1"
          >
            <LogOut className="w-3.5 h-3.5 stroke-[2]" />
          </button>
        </div>
      </div>
    </div>

    <WorkspaceModal
      isOpen={workspaceModalOpen}
      onClose={() => setWorkspaceModalOpen(false)}
    />

    {creditsModalOpen && (
      <div className="fixed inset-0 z-50 bg-stone-900/50 backdrop-blur-xs flex items-center justify-center p-4">
        <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-2xl p-6 max-w-md w-full shadow-xl">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-bold text-stone-900 dark:text-white">Gnani.ai API Credits</h3>
            <button onClick={() => setCreditsModalOpen(false)} className="text-stone-400 hover:text-stone-700 dark:hover:text-white text-lg font-bold cursor-pointer">×</button>
          </div>
          <div className="p-4 rounded-xl bg-stone-50 dark:bg-stone-800 border border-stone-200 dark:border-white/10 mb-4">
            <div className="flex justify-between text-xs text-stone-600 dark:text-stone-300 mb-1.5">
              <span>Speech & Telephony Minutes</span>
              <span className="font-semibold text-stone-900 dark:text-white">{credits?.used || 20} / {credits?.total || 1000} mins used</span>
            </div>
            <div className="w-full h-2 rounded-full bg-stone-200 dark:bg-stone-700 overflow-hidden">
              <div className="h-full bg-stone-900 dark:bg-white rounded-full" style={{ width: `${creditPct}%` }} />
            </div>
          </div>
          <div className="space-y-2.5 text-xs text-stone-600 dark:text-stone-300 mb-6">
            <div className="flex justify-between py-1 border-b border-stone-100 dark:border-white/5">
              <span>Speech Engine</span>
              <span className="font-medium text-stone-900 dark:text-white">Gnani.ai Vach / Timbre v2.5</span>
            </div>
            <div className="flex justify-between py-1 border-b border-stone-100 dark:border-white/5">
              <span>Audio Codec Format</span>
              <span className="font-medium text-stone-900 dark:text-white">8kHz µ-law (Native Telephony)</span>
            </div>
            <div className="flex justify-between py-1">
              <span>API Status</span>
              <span className="font-semibold text-emerald-700 dark:text-emerald-400">● Healthy & Active</span>
            </div>
          </div>
          <button onClick={() => setCreditsModalOpen(false)} className="w-full py-2 bg-stone-900 text-white dark:bg-white dark:text-stone-900 rounded-xl text-xs font-semibold hover:bg-stone-800 transition-colors cursor-pointer">Close</button>
        </div>
      </div>
    )}
  </>
  );
}
