'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Dashboard } from '@/components/Dashboard';
import { LandingPage } from '@/components/landing/LandingPage';
import { useAuth } from '@/lib/useAuth';

export default function Home() {
  const [currentView, setCurrentView] = useState<'landing' | 'console'>('landing');
  const { user, signIn } = useAuth();
  const pendingConsoleOpen = useRef(false);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const viewParam = params.get('view');
      if (viewParam === 'console' || viewParam === 'dashboard') {
        if (user) {
          setCurrentView('console');
        } else {
          pendingConsoleOpen.current = true;
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (user && pendingConsoleOpen.current) {
      pendingConsoleOpen.current = false;
      setCurrentView('console');
    }
  }, [user]);

  const handleOpenDashboard = () => {
    if (!user) {
      pendingConsoleOpen.current = true;
      signIn();
      return;
    }
    setCurrentView('console');
  };

  return (
    <>
      {currentView === 'landing' ? (
        <LandingPage onOpenDashboard={handleOpenDashboard} />
      ) : (
        <Dashboard onBackToLanding={() => setCurrentView('landing')} />
      )}
    </>
  );
}
