import type { TabId } from './store';

/**
 * Which dashboard tabs belong to which workflow.
 *
 * Common tabs are always visible (they mean the same thing for every call). Domain tabs appear only
 * when their workflow is the one being VIEWED, so the sidebar doesn't grow as we add workflows.
 *
 * This is a deliberately simple keyword map keyed on the workflow name — the "static map first"
 * step. When the workflow builder can store a tab list per workflow, this moves into the DB and the
 * lookup here just reads workflow.domain_tabs instead.
 */

// Domain-specific tabs that are hidden unless their workflow is active/viewed.
export const DOMAIN_TAB_IDS: TabId[] = ['tickets', 'escalations', 'health', 'sanjeevani-followups', 'sanjeevani-prescriptions', 'sanjeevani-feedback', 'sanjeevani-patients', 'sanjeevani-appointments', 'sanjeevani-orders'];

interface DomainRule {
  tabs: TabId[];
  match: RegExp;
}

// First matching rule wins its tabs; a workflow can match more than one rule (tabs are unioned).
const DOMAIN_RULES: DomainRule[] = [
  { tabs: ['sanjeevani-followups', 'sanjeevani-prescriptions'], match: /sanjeevani|prescription|medication|patient[-\s]*care/i },
  { tabs: ['health'], match: /clinic|medical|health|hospital|patient|doctor|care\b/i },
  { tabs: ['tickets', 'escalations'], match: /incident|symphony|service\s*desk|help\s*desk|it\s*support|ticket/i },
  // Future workflows (components not built yet):
  //   { tabs: ['orders'], match: /ecommerce|e-commerce|order|retail|shop|cart/i },
  //   { tabs: ['cases'],  match: /\bhr\b|human\s*resources|employee|recruit/i },
];

/** Domain tabs for a workflow name (empty = only common tabs show for this workflow). */
export function domainTabsForWorkflow(name?: string | null): TabId[] {
  const n = (name || '').toLowerCase();
  if (!n) return [];
  const out = new Set<TabId>();
  for (const rule of DOMAIN_RULES) {
    if (rule.match.test(n)) rule.tabs.forEach((t) => out.add(t));
  }
  return [...out];
}

/** True if a tab is a domain tab (i.e. gated behind a workflow). */
export function isDomainTab(tab: TabId): boolean {
  return DOMAIN_TAB_IDS.includes(tab);
}
