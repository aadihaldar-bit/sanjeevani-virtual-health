'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { useDashboardStore } from '@/lib/store';
import { useWebSocket } from '@/lib/websocket';
import { AppShell } from '@/components/shell/AppShell';
import { GnaniHomeView } from '@/components/GnaniHomeView';
import { AgentsView } from '@/components/AgentsView';
import { SpeechPlayground } from '@/components/SpeechPlayground';
import { LiveCallsSidebar } from '@/components/LiveCallsSidebar';
import { ConversationView } from '@/components/ConversationView';
import { CompletedCallsTable } from '@/components/CompletedCallsTable';
import { AnalyticsDashboard } from '@/components/AnalyticsDashboard';
import { EscalationTracker } from '@/components/EscalationTracker';
import { DiagnosticsPanel } from '@/components/DiagnosticsPanel';
import { KnowledgeBasePanel } from '@/components/KnowledgeBasePanel';
import { LiveOperationsCenter } from '@/components/LiveOperationsCenter';
import { SimulatorPanel } from '@/components/SimulatorPanel';
import { ChatbotPopup } from '@/components/ChatbotPopup';
import { APIKeysPanel } from '@/components/APIKeysPanel';
import HealthSystemView from '@/components/HealthSystemView';
import { AdminPanel } from '@/components/admin/AdminPanel';
import { GetStarted } from '@/components/onboarding/GetStarted';
import { FollowupsPanel } from '@/components/sanjeevani/FollowupsPanel';
import { PrescriptionsPanel } from '@/components/sanjeevani/PrescriptionsPanel';
import { FeedbackPanel } from '@/components/sanjeevani/FeedbackPanel';
import { PatientsPanel } from '@/components/sanjeevani/PatientsPanel';
import { AppointmentsPanel } from '@/components/sanjeevani/AppointmentsPanel';
import { OrdersPanel } from '@/components/sanjeevani/OrdersPanel';

// Disable SSR for TicketsPanel — it uses window.location and fetch which
// must only run on the client. SSR causes React hydration error #418.
const TicketsPanel = dynamic(
  () => import('@/components/TicketsPanel').then((m) => m.TicketsPanel),
  { ssr: false, loading: () => (
    <div className="flex-1 flex items-center justify-center bg-[#fbfbfa] dark:bg-[#08090e]">
      <div className="text-center">
        <div className="w-6 h-6 border-2 border-stone-200 dark:border-white/10 border-t-stone-800 dark:border-t-white rounded-full animate-spin mx-auto mb-2" />
        <p className="text-xs text-stone-500 dark:text-stone-400">Loading Symphony Incidents...</p>
      </div>
    </div>
  )}
);

interface DashboardProps {
  onBackToLanding?: () => void;
}

export function Dashboard({ onBackToLanding }: DashboardProps) {
  const { selectedTab } = useDashboardStore();
  useWebSocket();

  return (
    <>
      <AppShell onBackToLanding={onBackToLanding}>
        {selectedTab === 'home' && <GnaniHomeView onBackToLanding={onBackToLanding} />}
        {selectedTab === 'get-started' && <GetStarted />}
        {selectedTab === 'simulator' && <SimulatorPanel />}
        {selectedTab === 'agents' && <AgentsView />}
        {selectedTab === 'speech-stt' && <SpeechPlayground initialMode="stt" />}
        {selectedTab === 'speech-tts' && <SpeechPlayground initialMode="tts" />}
        {selectedTab === 'live' && <LiveView />}
        {selectedTab === 'completed' && <CompletedCallsTable />}
        {selectedTab === 'tickets' && <TicketsPanel />}
        {selectedTab === 'analytics' && <AnalyticsDashboard />}
        {selectedTab === 'knowledge' && <KnowledgeBasePanel />}
        {selectedTab === 'escalations' && <EscalationTracker />}
        {selectedTab === 'diagnostics' && <DiagnosticsPanel />}
        {selectedTab === 'health' && <HealthSystemView />}
        {selectedTab === 'sanjeevani-followups' && <FollowupsPanel />}
        {selectedTab === 'sanjeevani-prescriptions' && <PrescriptionsPanel />}
        {selectedTab === 'sanjeevani-feedback' && <FeedbackPanel />}
        {selectedTab === 'sanjeevani-patients' && <PatientsPanel />}
        {selectedTab === 'sanjeevani-appointments' && <AppointmentsPanel />}
        {selectedTab === 'sanjeevani-orders' && <OrdersPanel />}
        {selectedTab === 'api-keys' && <APIKeysPanel />}
        {selectedTab === 'admin' && <AdminPanel section="org" />}
        {selectedTab === 'admin-numbers' && <AdminPanel section="numbers" />}
        {selectedTab === 'admin-models' && <AdminPanel section="models" />}
        {selectedTab === 'admin-vector' && <AdminPanel section="vector" />}
        {selectedTab === 'admin-audit' && <AdminPanel section="audit" />}
      </AppShell>

      {/* Floating voice chatbot pop-up */}
      <ChatbotPopup />
    </>
  );
}

function LiveView() {
  const { selectedCallId } = useDashboardStore();

  return (
    <div className="flex-1 flex w-full h-full min-h-0 min-w-0 overflow-hidden">
      <LiveCallsSidebar />
      <div className="flex-1 flex flex-col h-full min-h-0 min-w-0 overflow-hidden bg-[#fbfbfa] dark:bg-[#08090e]">
        {selectedCallId ? (
          <ConversationView />
        ) : (
          <LiveOperationsCenter />
        )}
      </div>
    </div>
  );
}
