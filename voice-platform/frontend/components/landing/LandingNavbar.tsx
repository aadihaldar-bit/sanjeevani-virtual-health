'use client';

import React, { useState } from 'react';
import { ArrowRight, LogOut, ChevronDown, User, ShieldCheck } from 'lucide-react';
import { ThemeToggle } from '@/components/ThemeToggle';
import { STLLogo } from '@/components/STLLogo';
import { useAuth } from '@/lib/useAuth';

interface LandingNavbarProps {
  onOpenDashboard: () => void;
}

export function LandingNavbar({ onOpenDashboard }: LandingNavbarProps) {
  const { user, signIn, logOut } = useAuth();
  const [profileOpen, setProfileOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 w-full border-b border-stone-200/80 dark:border-white/10 bg-white/80 dark:bg-[#08090e]/80 backdrop-blur-md">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between relative">
        
        {/* Logo & Brand */}
        <div className="flex items-center flex-shrink-0">
          <STLLogo compact />
        </div>

        {/* Center Links - Absolutely centered */}
        <nav className="hidden md:flex items-center gap-8 text-xs font-semibold text-stone-600 dark:text-stone-300 absolute left-1/2 -translate-x-1/2">
          <a href="#specialists" className="hover:text-stone-900 dark:hover:text-white transition-colors">Specialists</a>
          <a href="#studio" className="hover:text-stone-900 dark:hover:text-white transition-colors">Studio</a>
          <a href="/docs" className="hover:text-stone-900 dark:hover:text-white transition-colors">API Docs</a>
        </nav>

        {/* Right Actions */}
        <div className="flex items-center gap-2.5 sm:gap-3 flex-shrink-0">
          <ThemeToggle />

          {/* User Sign-In / Profile State */}
          {user ? (
            <div className="relative">
              <button
                onClick={() => setProfileOpen(!profileOpen)}
                type="button"
                className="flex items-center gap-2 h-9 px-3 rounded-full bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/20 hover:border-stone-300 dark:hover:border-white/40 shadow-xs transition-all cursor-pointer text-xs font-semibold text-stone-900 dark:text-white"
              >
                <div className="relative flex-shrink-0">
                  {user.photoURL ? (
                    <img
                      src={user.photoURL}
                      alt={user.displayName}
                      className="w-6 h-6 rounded-full object-cover ring-1 ring-black/10 dark:ring-white/20"
                    />
                  ) : (
                    <div className="w-6 h-6 rounded-full bg-gradient-to-tr from-indigo-600 via-indigo-500 to-purple-600 text-white flex items-center justify-center text-[10px] font-bold shadow-xs">
                      {user.initials || 'U'}
                    </div>
                  )}
                  {/* Google Logo Mini-Badge */}
                  <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-white dark:bg-stone-800 flex items-center justify-center shadow-xs border border-stone-200 dark:border-white/10">
                    <svg className="w-2 h-2" viewBox="0 0 24 24">
                      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
                      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
                    </svg>
                  </div>
                </div>
                <span className="hidden sm:inline font-semibold">{user.displayName}</span>
                <ChevronDown className="w-3.5 h-3.5 text-stone-400" />
              </button>

              {profileOpen && (
                <div className="absolute right-0 mt-2 w-60 bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/15 rounded-2xl shadow-2xl p-2 z-50 animate-in fade-in slide-in-from-top-2 duration-150 backdrop-blur-xl">
                  <div className="px-3 py-2.5 border-b border-stone-100 dark:border-white/10 mb-1">
                    <p className="text-xs font-bold text-stone-900 dark:text-white truncate">{user.displayName}</p>
                    <p className="text-[11px] text-stone-500 dark:text-stone-400 truncate">{user.email}</p>
                    <div className="mt-2 flex items-center gap-1.5 text-[10px] text-emerald-600 dark:text-emerald-400 font-semibold">
                      <ShieldCheck className="w-3.5 h-3.5" />
                      <span>Verified Google Account</span>
                    </div>
                  </div>

                  <button
                    onClick={() => {
                      setProfileOpen(false);
                      onOpenDashboard();
                    }}
                    type="button"
                    className="w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs font-semibold text-stone-800 dark:text-stone-200 hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors cursor-pointer text-left"
                  >
                    <span>Enterprise Console</span>
                    <ArrowRight className="w-3.5 h-3.5 text-stone-400" />
                  </button>

                  <button
                    onClick={() => {
                      setProfileOpen(false);
                      logOut();
                    }}
                    type="button"
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors cursor-pointer text-left mt-1"
                  >
                    <LogOut className="w-3.5 h-3.5" />
                    <span>Sign Out</span>
                  </button>
                </div>
              )}
            </div>
          ) : (
            <button
              onClick={() => signIn()}
              type="button"
              className="inline-flex items-center gap-2 h-9 px-3.5 rounded-full border border-stone-300 dark:border-white/20 bg-white dark:bg-stone-900 text-xs font-semibold text-stone-800 dark:text-stone-200 hover:bg-stone-50 dark:hover:bg-stone-800 shadow-sm transition-all cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
              </svg>
              <span>Sign in with Google</span>
            </button>
          )}

          {/* Launch Console CTA */}
          <button
            onClick={onOpenDashboard}
            type="button"
            className="inline-flex items-center gap-1.5 h-9 px-4 rounded-full bg-stone-900 text-white dark:bg-white dark:text-stone-900 text-xs font-semibold shadow-md hover:scale-105 active:scale-95 transition-all cursor-pointer"
          >
            <span>Launch Console</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>

      </div>
    </header>
  );
}
