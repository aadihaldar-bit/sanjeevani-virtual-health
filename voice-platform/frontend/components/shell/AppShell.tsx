'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { useDashboardStore } from '@/lib/store';
import { useThemeStore } from '@/lib/themeStore';
import { useShellStore } from '@/lib/shellStore';
import { useWorkspace } from '@/lib/useMe';
import { useAgents } from '@/lib/useAgents';
import { useViewedCompany } from '@/lib/useViewedCompany';
import { onboardingFlags } from '@/lib/useOnboarding';
import { domainTabsForWorkflow } from '@/lib/workflowTabs';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { SectionTabs } from './SectionTabs';
import { ArchitectPanel } from './ArchitectPanel';
import { CommandPalette } from './CommandPalette';
import { NewAgentDialog } from '@/components/agents/NewAgentDialog';
import { WelcomeDialog } from '@/components/onboarding/GetStarted';
import { buildSections, landingTab, rememberTab, sectionForTab } from './nav';
import type { Section } from './nav';

interface Props {
  onBackToLanding?: () => void;
  children: React.ReactNode;
}

/**
 * The frame around every screen: a sidebar (full, or icons only), a top bar, a bar of sub-pages for the section you are
 * in, and Architect on the right when it is open. The screens themselves render inside.
 */
export function AppShell({ onBackToLanding, children }: Props) {
  const { selectedTab, setSelectedTab } = useDashboardStore();
  const { me, canManage, isPlatform } = useWorkspace();
  const { agents } = useAgents();
  const setTheme = useThemeStore((s) => s.setTheme);
  const { architectOpen, hydrate } = useShellStore();
  const [menuOpen, setMenuOpen] = useState(false);

  // Restore the saved light/dark choice and sidebar width once.
  useEffect(() => {
    try {
      const saved = localStorage.getItem('stl_theme');
      setTheme(saved === 'dark' ? 'dark' : 'light');
    } catch { setTheme('light'); }
    hydrate();
  }, [setTheme, hydrate]);

  const isAdmin = !!me && (me.is_super_admin || me.role === 'tenant_admin');
  // Appointments come from STL's own clinic receptionist (with its identity check). A company's booking agent has no
  // such records behind it, so the tab would only ever be empty for them.
  // (and only in STL's own view: a client being looked at as an administrator gets the client's pages, not STL's)
  const { isOwnPlatform } = useViewedCompany();
  const showAppointments = isPlatform && isOwnPlatform && agents.some((a) => domainTabsForWorkflow(a.name).includes('health'));

  const showPatientCare = agents.some((a) => domainTabsForWorkflow(a.name).includes('sanjeevani-followups'));

  const showTickets = !showPatientCare;
  const sections = useMemo(() => buildSections({ canManage, isAdmin, showAppointments, showPatientCare, showTickets }), [canManage, isAdmin, showAppointments, showPatientCare, showTickets]);
  const section = sectionForTab(selectedTab, sections);

  useEffect(() => { rememberTab(section.id, selectedTab); }, [section.id, selectedTab]);
  useEffect(() => { if (selectedTab === 'simulator') onboardingFlags.triedSimulator(); }, [selectedTab]);

  // The Appointments tab disappears if the last healthcare agent is deleted while you are on it.
  useEffect(() => {
    if (selectedTab === 'health' && !showAppointments && agents.length > 0) setSelectedTab('tickets');
  }, [selectedTab, showAppointments, agents.length, setSelectedTab]);

  // A page this company does not have (its agents do not use it) is never left on screen: go to the section's first page.
  useEffect(() => {
    if (agents.length === 0) return;
    const all = sections.flatMap((s) => s.tabs.map((t) => t.id));
    if (['tickets', 'health', 'sanjeevani-followups', 'sanjeevani-prescriptions', 'sanjeevani-feedback', 'sanjeevani-patients', 'sanjeevani-appointments', 'sanjeevani-orders'].includes(selectedTab) && !all.includes(selectedTab)) {
      const records = sections.find((s) => s.id === 'records');
      setSelectedTab(records ? records.tabs[0].id : 'home');
    }
  }, [selectedTab, sections, agents.length, setSelectedTab]);

  const go = (s: Section) => { setSelectedTab(landingTab(s)); setMenuOpen(false); };

  return (
    <div className="h-screen flex overflow-hidden bg-[#f7f7f5] dark:bg-[#08090e] font-sans text-stone-900 dark:text-stone-100">
      <Sidebar
        sections={sections}
        activeId={section.id}
        agents={agents}
        canManage={canManage}
        onNavigate={go}
        onHome={() => setSelectedTab('home')}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
      />
      <div className="flex-1 min-w-0 flex flex-col">
        <TopBar title={section.label} onMenu={() => setMenuOpen(true)} onBackToLanding={onBackToLanding} />
        <SectionTabs tabs={section.tabs} active={selectedTab} onSelect={setSelectedTab} />
        <main className="flex-1 min-h-0 flex overflow-hidden">{children}</main>
      </div>
      {architectOpen && canManage && <ArchitectPanel />}

      <CommandPalette sections={sections} agents={agents} canManage={canManage} onGo={setSelectedTab} />
      {canManage && <NewAgentDialog />}
      <WelcomeDialog />
    </div>
  );
}
