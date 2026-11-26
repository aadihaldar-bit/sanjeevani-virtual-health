import { create } from 'zustand';

/**
 * State shared by the pieces of the console frame: the sidebar (full or icons only), the Architect panel on the right,
 * the new-agent dialog and the search palette. Opening Architect tucks the sidebar down to icons, as it frees the room
 * the chat needs; closing it puts the sidebar back the way the person had it.
 */
const KEY = 'stl_sidebar_collapsed';

function saved(): boolean {
  try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
}

interface ShellState {
  collapsed: boolean;
  architectOpen: boolean;
  newAgentOpen: boolean;
  newAgentPreset: string | null;      // a template id to start from
  paletteOpen: boolean;
  welcomeOpen: boolean;
  /** something the palette or a button wants Architect to answer as soon as it opens */
  pendingPrompt: string | null;
  /** bumped whenever an agent is created, changed or deleted, so lists elsewhere refetch */
  agentsVersion: number;

  hydrate: () => void;
  toggleSidebar: () => void;
  openArchitect: () => void;
  closeArchitect: () => void;
  toggleArchitect: () => void;
  openNewAgent: (preset?: string | null) => void;
  closeNewAgent: () => void;
  setPalette: (open: boolean) => void;
  setWelcome: (open: boolean) => void;
  askArchitect: (text: string) => void;
  clearPrompt: () => void;
  agentsChanged: () => void;
}

let restoreTo: boolean | null = null;      // what the sidebar was before Architect took the space

export const useShellStore = create<ShellState>((set, get) => ({
  collapsed: false,
  architectOpen: false,
  newAgentOpen: false,
  newAgentPreset: null,
  paletteOpen: false,
  welcomeOpen: false,
  pendingPrompt: null,
  agentsVersion: 0,

  hydrate: () => set({ collapsed: saved() }),
  toggleSidebar: () => {
    const next = !get().collapsed;
    try { localStorage.setItem(KEY, next ? '1' : '0'); } catch { /* not remembered */ }
    restoreTo = null;
    set({ collapsed: next });
  },
  openArchitect: () => {
    if (get().architectOpen) return;
    restoreTo = get().collapsed;
    set({ architectOpen: true, collapsed: true });
  },
  closeArchitect: () => {
    const back = restoreTo;
    restoreTo = null;
    set({ architectOpen: false, ...(back === null ? {} : { collapsed: back }) });
  },
  toggleArchitect: () => (get().architectOpen ? get().closeArchitect() : get().openArchitect()),
  openNewAgent: (preset = null) => set({ newAgentOpen: true, newAgentPreset: preset, paletteOpen: false }),
  closeNewAgent: () => set({ newAgentOpen: false, newAgentPreset: null }),
  setPalette: (open) => set({ paletteOpen: open }),
  setWelcome: (open) => set({ welcomeOpen: open }),
  askArchitect: (text) => { get().openArchitect(); set({ pendingPrompt: text, paletteOpen: false }); },
  clearPrompt: () => set({ pendingPrompt: null }),
  agentsChanged: () => set((s) => ({ agentsVersion: s.agentsVersion + 1 })),
}));
