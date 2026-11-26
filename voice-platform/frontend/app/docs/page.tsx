'use client';

import React, { useState, useEffect } from 'react';
import { API_CATEGORIES, EndpointDoc } from '@/lib/docsData';
import { DocsNavbar } from '@/components/docs/DocsNavbar';
import { DocsSidebar } from '@/components/docs/DocsSidebar';
import { DocsContent } from '@/components/docs/DocsContent';
import { DocsPlayground } from '@/components/docs/DocsPlayground';
import { Menu, Terminal, Layers } from 'lucide-react';

export default function ApiDocsPage() {
  // Flatten all endpoints for quick lookup
  const allEndpoints = API_CATEGORIES.flatMap((c) => c.endpoints);

  const [activeEndpoint, setActiveEndpoint] = useState<EndpointDoc>(allEndpoints[0]);
  const [searchQuery, setSearchQuery] = useState('');
  const [host, setHost] = useState('https://voicebot-agent.stldigitaltech.com');
  const [token, setToken] = useState('demo_token');
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);

  // Sync with window hash if provided e.g. #speech-tts
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const hash = window.location.hash.replace('#', '');
      if (hash) {
        const found = allEndpoints.find((e) => e.id === hash || e.categorySlug === hash);
        if (found) {
          setActiveEndpoint(found);
        }
      }
    }
  }, []);

  const handleSelectEndpoint = (endpoint: EndpointDoc) => {
    setActiveEndpoint(endpoint);
    if (typeof window !== 'undefined') {
      window.history.replaceState(null, '', `#${endpoint.id}`);
    }
  };

  return (
    <div className="min-h-screen bg-[#fafafa] dark:bg-[#08090d] text-stone-900 dark:text-stone-100 font-sans transition-colors">
      {/* Top Navbar */}
      <DocsNavbar
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
      />

      {/* Mobile Bar */}
      <div className="lg:hidden flex items-center justify-between px-4 py-2.5 bg-stone-100 dark:bg-stone-900/80 border-b border-stone-200 dark:border-white/10 text-xs">
        <button
          onClick={() => setIsMobileSidebarOpen(true)}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white dark:bg-stone-800 border border-stone-200 dark:border-white/10 font-medium text-stone-700 dark:text-stone-200"
        >
          <Menu className="w-3.5 h-3.5" />
          <span>All Endpoints</span>
        </button>

        <div className="flex items-center gap-1.5 font-mono text-[11px] text-stone-500 dark:text-stone-400 truncate max-w-[200px]">
          <span className="font-bold text-indigo-600 dark:text-indigo-400">
            {activeEndpoint.method}
          </span>
          <span className="truncate">{activeEndpoint.path}</span>
        </div>
      </div>

      {/* Main 3-Column Layout */}
      <div className="max-w-[1720px] mx-auto flex flex-col lg:flex-row min-h-[calc(100vh-3.5rem)]">
        {/* Left Column: Navigation Sidebar */}
        <DocsSidebar
          categories={API_CATEGORIES}
          activeEndpointId={activeEndpoint.id}
          onSelectEndpoint={handleSelectEndpoint}
          searchQuery={searchQuery}
          isOpenMobile={isMobileSidebarOpen}
          onCloseMobile={() => setIsMobileSidebarOpen(false)}
        />

        {/* Center & Right Column Container */}
        <div className="flex-1 lg:ml-64 xl:ml-72 flex flex-col xl:flex-row justify-between min-w-0">
          {/* Center Column: Documentation Content */}
          <main className="flex-1 min-w-0 bg-[#fdfdfd] dark:bg-[#090b10]">
            <DocsContent endpoint={activeEndpoint} host={host} />
          </main>

          {/* Right Column: Code & Interactive Playground */}
          <aside className="w-full xl:w-[480px] flex-shrink-0">
            <DocsPlayground
              endpoint={activeEndpoint}
              host={host}
              onHostChange={setHost}
              token={token}
              onTokenChange={setToken}
            />
          </aside>
        </div>
      </div>
    </div>
  );
}
