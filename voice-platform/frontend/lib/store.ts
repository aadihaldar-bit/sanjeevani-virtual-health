import { create } from 'zustand';
import type { CallData, CallDetail, AnalyticsSummary, EscalationData, DailyData, TicketData } from './api';

// ─── Types ─────────────────────────────────────────────────────

export type TabId =
  | 'home'
  | 'simulator'
  | 'agents'
  | 'speech-stt'
  | 'speech-tts'
  | 'live'
  | 'completed'
  | 'tickets'
  | 'analytics'
  | 'escalations'
  | 'diagnostics'
  | 'knowledge'
  | 'health'
  | 'sanjeevani-followups'
  | 'sanjeevani-prescriptions'
  | 'sanjeevani-feedback'
  | 'sanjeevani-patients'
  | 'sanjeevani-appointments'
  | 'sanjeevani-orders'
  | 'api-keys'
  | 'admin'
  | 'admin-numbers'
  | 'admin-models'
  | 'admin-vector'
  | 'admin-audit'
  | 'get-started';

// ─── Store ─────────────────────────────────────────────────────

interface DashboardState {
  // Navigation
  selectedTab: TabId;
  setSelectedTab: (tab: TabId) => void;

  // Which workflow the dashboard is VIEWING (drives which domain tabs show). This is a view-only
  // preference — it does NOT change which workflow is live on the phones (Workflow.is_active).
  // null = follow the system active workflow.
  viewWorkflowId: number | null;
  viewWorkflowName: string | null;
  setViewWorkflow: (id: number | null, name: string | null) => void;

  // Active calls (from WebSocket initial state + events)
  activeCalls: CallData[];
  setActiveCalls: (calls: CallData[]) => void;
  upsertActiveCall: (call: CallData) => void;
  removeActiveCall: (callId: number) => void;

  // Completed calls (from REST API)
  completedCalls: CallData[];
  completedTotal: number;
  setCompletedCalls: (calls: CallData[], total: number) => void;

  // Selected call detail
  selectedCallId: number | null;
  selectedCallDetail: CallDetail | null;
  selectCall: (callId: number | null) => void;
  setCallDetail: (detail: CallDetail | null) => void;
  /** A line of a live call's transcript, as it is said (or a link the agent has just given). Ignored unless that call is open. */
  appendTranscript: (callId: number, msg: { role: string; message: string; sequence_number?: number; timestamp?: string }) => void;
  applyTicketUpdate: (ticket: TicketData, call?: CallData | CallDetail | null) => void;

  // Analytics
  analytics: AnalyticsSummary | null;
  dailyData: DailyData[];
  setAnalytics: (data: AnalyticsSummary) => void;
  setDailyData: (data: DailyData[]) => void;

  // Escalations
  escalations: EscalationData[];
  setEscalations: (data: EscalationData[]) => void;

  // WebSocket connection
  connected: boolean;
  connectionStatus: 'connecting' | 'connected' | 'disconnected' | 'error';
  setConnected: (connected: boolean) => void;
  setConnectionStatus: (status: 'connecting' | 'connected' | 'disconnected' | 'error') => void;

  // Data refresh trigger
  refreshCounter: number;
  triggerRefresh: () => void;
}

const _storedViewId =
  typeof window !== 'undefined' ? Number(localStorage.getItem('stl_view_workflow_id')) : NaN;

export const useDashboardStore = create<DashboardState>((set) => ({
  // Navigation
  selectedTab: 'home',
  setSelectedTab: (tab) => set({ selectedTab: tab }),

  viewWorkflowId: Number.isFinite(_storedViewId) && _storedViewId > 0 ? _storedViewId : null,
  viewWorkflowName: null,
  setViewWorkflow: (id, name) => {
    if (typeof window !== 'undefined') {
      if (id) localStorage.setItem('stl_view_workflow_id', String(id));
      else localStorage.removeItem('stl_view_workflow_id');
    }
    set({ viewWorkflowId: id, viewWorkflowName: name });
  },

  // Active calls
  activeCalls: [],
  setActiveCalls: (calls) =>
    set(() => ({
      activeCalls: [...calls].sort(
        (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
      ),
    })),
  upsertActiveCall: (call) =>
    set((state) => ({
      activeCalls: [call, ...state.activeCalls.filter((c) => c.id !== call.id)].sort(
        (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
      ),
    })),
  removeActiveCall: (callId) =>
    set((state) => ({
      activeCalls: state.activeCalls.filter((c) => c.id !== callId),
    })),

  // Completed calls
  completedCalls: [],
  completedTotal: 0,
  setCompletedCalls: (calls, total) => set({ completedCalls: calls, completedTotal: total }),

  // Selected call
  selectedCallId: null,
  selectedCallDetail: null,
  selectCall: (callId) => set({ selectedCallId: callId, selectedCallDetail: null }),
  setCallDetail: (detail) => set({ selectedCallDetail: detail }),
  appendTranscript: (callId, msg) =>
    set((state) => {
      const d = state.selectedCallDetail;
      if (!d || d.id !== callId || !msg.message) return {};
      const have = d.transcript || [];
      if (have.some((m) => m.role === msg.role && m.message === msg.message)) return {};      // already fetched with the call
      const next = {
        id: -(have.length + 1), role: (msg.role === 'user' ? 'user' : 'agent') as 'user' | 'agent', message: msg.message,
        timestamp: msg.timestamp || new Date().toISOString(), sequence_number: msg.sequence_number ?? have.length,
      };
      return { selectedCallDetail: { ...d, transcript: [...have, next] } };
    }),
  applyTicketUpdate: (ticket, call) =>
    set((state) => {
      const applyToCall = <T extends CallData>(item: T): T =>
        item.id === ticket.call_id ? ({ ...item, latest_ticket: ticket } as T) : item;

      const selectedCallDetail =
        state.selectedCallDetail && state.selectedCallDetail.id === ticket.call_id
          ? { ...state.selectedCallDetail, latest_ticket: ticket }
          : state.selectedCallDetail;

      return {
        activeCalls: state.activeCalls.map(applyToCall),
        completedCalls: state.completedCalls.map(applyToCall),
        selectedCallDetail: call
          ? ({ ...(selectedCallDetail ?? {}), ...call, latest_ticket: ticket } as CallDetail)
          : selectedCallDetail,
      };
    }),

  // Analytics
  analytics: null,
  dailyData: [],
  setAnalytics: (data) => set({ analytics: data }),
  setDailyData: (data) => set({ dailyData: data }),

  // Escalations
  escalations: [],
  setEscalations: (data) => set({ escalations: data }),

  // WebSocket
  connected: false,
  connectionStatus: 'disconnected',
  setConnected: (connected) => set({ connected }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),

  // Refresh trigger
  refreshCounter: 0,
  triggerRefresh: () => set((state) => ({ refreshCounter: state.refreshCounter + 1 })),
}));
