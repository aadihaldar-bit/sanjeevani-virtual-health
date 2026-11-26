'use client';

import React from 'react';
import {
  CategoryGroup,
  EndpointDoc,
} from '@/lib/docsData';
import {
  ShieldCheck,
  PhoneCall,
  Volume2,
  Radio,
  Server,
  BookOpen,
  Ticket,
  BarChart3,
  ChevronRight,
  Sparkles,
} from 'lucide-react';

interface DocsSidebarProps {
  categories: CategoryGroup[];
  activeEndpointId: string;
  onSelectEndpoint: (endpoint: EndpointDoc) => void;
  searchQuery: string;
  isOpenMobile: boolean;
  onCloseMobile: () => void;
}

const ICON_MAP: Record<string, React.ReactNode> = {
  ShieldCheck: <ShieldCheck className="w-3.5 h-3.5" />,
  PhoneCall: <PhoneCall className="w-3.5 h-3.5" />,
  Volume2: <Volume2 className="w-3.5 h-3.5" />,
  Radio: <Radio className="w-3.5 h-3.5" />,
  Server: <Server className="w-3.5 h-3.5" />,
  BookOpen: <BookOpen className="w-3.5 h-3.5" />,
  Ticket: <Ticket className="w-3.5 h-3.5" />,
  BarChart3: <BarChart3 className="w-3.5 h-3.5" />,
};

export function DocsSidebar({
  categories,
  activeEndpointId,
  onSelectEndpoint,
  searchQuery,
  isOpenMobile,
  onCloseMobile,
}: DocsSidebarProps) {
  // Method badge style helper
  const getMethodBadge = (method: string) => {
    switch (method) {
      case 'GET':
        return 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-800/30';
      case 'POST':
        return 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/40 border-blue-200 dark:border-blue-800/30';
      case 'WS':
        return 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-800/30';
      case 'PATCH':
        return 'text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-950/40 border-purple-200 dark:border-purple-800/30';
      case 'DELETE':
        return 'text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40 border-rose-200 dark:border-rose-800/30';
      default:
        return 'text-stone-600 dark:text-stone-400 bg-stone-100 dark:bg-stone-800 border-stone-200';
    }
  };

  const filteredCategories = categories
    .map((cat) => {
      const q = searchQuery.toLowerCase().trim();
      if (!q) return cat;

      const matchingEndpoints = cat.endpoints.filter(
        (ep) =>
          ep.title.toLowerCase().includes(q) ||
          ep.path.toLowerCase().includes(q) ||
          ep.method.toLowerCase().includes(q) ||
          ep.description.toLowerCase().includes(q) ||
          (ep.bodyParams && ep.bodyParams.some((p) => p.name.toLowerCase().includes(q)))
      );

      return {
        ...cat,
        endpoints: matchingEndpoints,
      };
    })
    .filter((cat) => cat.endpoints.length > 0);

  return (
    <>
      {/* Mobile Backdrop */}
      {isOpenMobile && (
        <div
          className="fixed inset-0 z-30 bg-black/40 backdrop-blur-xs lg:hidden"
          onClick={onCloseMobile}
        />
      )}

      <aside
        className={`
          fixed top-14 bottom-0 left-0 z-30 w-72 lg:w-64 xl:w-72
          bg-[#fafafa] dark:bg-[#0c0e12] border-r border-stone-200 dark:border-white/10
          overflow-y-auto overscroll-contain px-3.5 py-4 transition-transform duration-200 ease-in-out
          lg:translate-x-0 ${isOpenMobile ? 'translate-x-0 shadow-2xl' : '-translate-x-full'}
        `}
      >
        {/* Quick Intro Banner */}
        <div className="mb-4 p-2.5 rounded-xl bg-gradient-to-br from-indigo-50/70 to-violet-50/40 dark:from-indigo-950/30 dark:to-stone-900 border border-indigo-100/80 dark:border-indigo-900/30">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold text-indigo-700 dark:text-indigo-300 mb-1">
            <Sparkles className="w-3.5 h-3.5 text-indigo-500" />
            <span>Fern-Style Interactive Docs</span>
          </div>
          <p className="text-[11px] text-stone-600 dark:text-stone-400 leading-relaxed font-sans">
            Real backend endpoints with live parameter playground &amp; code generation.
          </p>
        </div>

        {/* Navigation Categories */}
        <nav className="space-y-5">
          {filteredCategories.map((category) => (
            <div key={category.slug} className="space-y-1">
              <div className="flex items-center gap-2 px-2 py-1 text-[11px] font-semibold text-stone-400 dark:text-stone-400 uppercase tracking-wider">
                <span className="text-stone-400 dark:text-stone-500">
                  {ICON_MAP[category.icon] || <ChevronRight className="w-3.5 h-3.5" />}
                </span>
                <span>{category.name}</span>
                <span className="ml-auto text-[10px] font-mono text-stone-400 dark:text-stone-400">
                  {category.endpoints.length}
                </span>
              </div>

              <div className="space-y-0.5">
                {category.endpoints.map((endpoint) => {
                  const isActive = endpoint.id === activeEndpointId;
                  return (
                    <button
                      key={endpoint.id}
                      onClick={() => {
                        onSelectEndpoint(endpoint);
                        onCloseMobile();
                      }}
                      className={`
                        w-full text-left group flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-xs transition-all duration-150
                        ${
                          isActive
                            ? 'bg-indigo-50/90 dark:bg-indigo-950/50 text-indigo-950 dark:text-indigo-200 font-semibold shadow-2xs border border-indigo-200/60 dark:border-indigo-800/40'
                            : 'text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-stone-200 hover:bg-stone-100 dark:hover:bg-white/5'
                        }
                      `}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <span
                          className={`
                            px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase rounded border flex-shrink-0 tracking-wider
                            ${getMethodBadge(endpoint.method)}
                          `}
                        >
                          {endpoint.method}
                        </span>
                        <span className="truncate">{endpoint.title}</span>
                      </div>

                      {endpoint.latencySla && (
                        <span
                          className={`
                            text-[10px] font-mono flex-shrink-0 px-1 py-0.5 rounded
                            ${
                              isActive
                                ? 'text-indigo-600 dark:text-indigo-400'
                                : 'text-stone-400 dark:text-stone-400 group-hover:text-stone-600 dark:group-hover:text-stone-300'
                            }
                          `}
                        >
                          {endpoint.latencySla}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}

          {filteredCategories.length === 0 && (
            <div className="text-center py-8 px-4">
              <p className="text-xs text-stone-500 dark:text-stone-400">
                No endpoints found matching &ldquo;{searchQuery}&rdquo;
              </p>
            </div>
          )}
        </nav>
      </aside>
    </>
  );
}
