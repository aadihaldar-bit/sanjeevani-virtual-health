import { Home, Bot, Phone, ClipboardList, BarChart3, BookOpen, Wrench, Settings } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { TabId } from '@/lib/store';

export interface SubTab { id: TabId; label: string; }
export interface Section {
  id: string;
  label: string;
  icon: LucideIcon;
  tabs: SubTab[];
}

export interface NavContext {
  /** may change agents, knowledge and API keys */
  canManage: boolean;
  /** may open the Team & access screens */
  isAdmin: boolean;
  /** the company has a healthcare agent, so booked appointments are worth a page */
  showAppointments: boolean;
  /** the company has a patient-care agent (prescription follow-up calls), so its prescriptions and follow-up calls get pages */
  showPatientCare?: boolean;
  /** tickets are for companies whose agents raise them; a patient-care company has its own pages instead */
  showTickets?: boolean;
}

/**
 * The whole console in seven places. Each place can have sub-tabs, shown as a bar at the top of the page, so the
 * sidebar stays short and related screens sit together (live calls next to call history, not in different groups).
 */
export function buildSections(ctx: NavContext): Section[] {
  const sections: Section[] = [
    { id: 'home', label: 'Home', icon: Home, tabs: [{ id: 'home', label: 'Overview' }, { id: 'get-started', label: 'Get started' }] },
    { id: 'agents', label: 'Agents', icon: Bot, tabs: [{ id: 'agents', label: 'My agents' }] },
    {
      id: 'calls', label: 'Calls', icon: Phone,
      tabs: [
        { id: 'live', label: 'Live' },
        { id: 'completed', label: 'History' },
        { id: 'diagnostics', label: 'Diagnostics' },
      ],
    },
    // What the agents produce: tickets they raise (or you create by hand), calls handed to a person, appointments.
    {
      id: 'records', label: 'Records', icon: ClipboardList,
      tabs: [
        ...(ctx.showTickets === false ? [] : [{ id: 'tickets' as TabId, label: 'Tickets' }]),
        { id: 'escalations', label: 'Handed over' },
        ...(ctx.showAppointments ? [{ id: 'health' as TabId, label: 'Appointments' }] : []),
        ...(ctx.showPatientCare ? [{ id: 'sanjeevani-patients' as TabId, label: 'Patients' }, { id: 'sanjeevani-appointments' as TabId, label: 'Appointments' }, { id: 'sanjeevani-orders' as TabId, label: 'Medicine orders' }, { id: 'sanjeevani-prescriptions' as TabId, label: 'Prescriptions' }, { id: 'sanjeevani-followups' as TabId, label: 'Follow-up calls' }, { id: 'sanjeevani-feedback' as TabId, label: 'Patient feedback' }] : []),
      ],
    },
    { id: 'analytics', label: 'Analytics', icon: BarChart3, tabs: [{ id: 'analytics', label: 'Overview' }] },
    { id: 'knowledge', label: 'Knowledge', icon: BookOpen, tabs: [{ id: 'knowledge', label: 'Documents' }] },
    {
      id: 'tools', label: 'Tools', icon: Wrench,
      tabs: [
        { id: 'simulator', label: 'Agent simulator' },
        { id: 'speech-stt', label: 'Speech to text' },
        { id: 'speech-tts', label: 'Text to speech' },
      ],
    },
  ];
  const settings: SubTab[] = [];
  if (ctx.isAdmin) {
    settings.push({ id: 'admin', label: 'Team & access' });
    settings.push({ id: 'admin-numbers', label: 'Phone numbers' });
    settings.push({ id: 'admin-models', label: 'Models & keys' });
    settings.push({ id: 'admin-vector', label: 'Vector database' });
    settings.push({ id: 'admin-audit', label: 'Audit log' });
  }
  if (ctx.canManage) settings.push({ id: 'api-keys', label: 'API keys' });
  if (settings.length) sections.push({ id: 'settings', label: 'Settings', icon: Settings, tabs: settings });
  return sections;
}

/** How the sidebar groups the sections under small headings. Sections not listed here are not shown. */
export const SIDEBAR_GROUPS: { label: string | null; ids: string[] }[] = [
  { label: null, ids: ['home'] },
  { label: 'Build', ids: ['agents', 'knowledge', 'tools'] },
  { label: 'Monitor', ids: ['calls', 'records', 'analytics'] },
  { label: 'Manage', ids: ['settings'] },
];

/** The section a tab lives in (home when nothing matches, so an unknown tab never leaves a blank shell). */
export function sectionForTab(tab: TabId, sections: Section[]): Section {
  return sections.find((s) => s.tabs.some((t) => t.id === tab)) || sections[0];
}

// Remember where the person was in each section, so clicking back into "Calls" returns to "History", not "Live".
const lastTab: Record<string, TabId> = {};

export function rememberTab(sectionId: string, tab: TabId) {
  lastTab[sectionId] = tab;
}

export function landingTab(section: Section): TabId {
  const remembered = lastTab[section.id];
  return remembered && section.tabs.some((t) => t.id === remembered) ? remembered : section.tabs[0].id;
}
