'use client';

import React, { useEffect } from 'react';
import { Sun, Moon } from 'lucide-react';
import { useThemeStore } from '@/lib/themeStore';

export function ThemeToggle({ className = '' }: { className?: string }) {
  const { theme, toggleTheme, setTheme } = useThemeStore();

  useEffect(() => {
    const saved = localStorage.getItem('stl_theme') as 'light' | 'dark' | null;
    if (saved) {
      setTheme(saved);
    } else {
      setTheme('light');
    }
  }, [setTheme]);

  return (
    <button
      onClick={toggleTheme}
      type="button"
      title={theme === 'light' ? 'Switch to Dark Studio' : 'Switch to Light Mode'}
      className={`relative inline-flex items-center gap-1.5 h-9 px-3 rounded-full text-xs font-medium transition-all duration-300 border cursor-pointer ${
        theme === 'dark'
          ? 'bg-[#141721] text-stone-300 border-white/10 hover:border-white/20 hover:text-white shadow-sm'
          : 'bg-stone-100 text-stone-700 border-stone-200 hover:border-stone-300 hover:bg-stone-200/70 shadow-sm'
      } ${className}`}
    >
      {theme === 'light' ? (
        <>
          <Sun className="w-3.5 h-3.5 text-amber-500" />
          <span className="font-medium">Light</span>
        </>
      ) : (
        <>
          <Moon className="w-3.5 h-3.5 text-indigo-400" />
          <span className="font-medium">Dark</span>
        </>
      )}
    </button>
  );
}
