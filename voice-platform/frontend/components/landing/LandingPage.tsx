'use client';

import React from 'react';
import { ArrowRight, Headphones } from 'lucide-react';
import { LandingNavbar } from './LandingNavbar';
import { VoiceSphereCarousel } from './VoiceSphereCarousel';
import { IndustryShowcase } from './IndustryShowcase';
import { ProductStack } from './ProductStack';
import { AgentArchitecture } from './AgentArchitecture';
import { CapabilityTabs } from './CapabilityTabs';
import { MultilingualGlobe } from './MultilingualGlobe';
import { SecuritySection } from './SecuritySection';
import { CreativeVoiceStudio } from './CreativeVoiceStudio';
import { EnterpriseTrustBar } from './EnterpriseTrustBar';
import { InteractiveVoiceChatWidget } from './InteractiveVoiceChatWidget';
import { LandingFooter } from './LandingFooter';
import { Reveal } from './Reveal';
import { useAuth } from '@/lib/useAuth';

interface LandingPageProps {
  onOpenDashboard: () => void;
}

export function LandingPage({ onOpenDashboard }: LandingPageProps) {
  const { user, signIn } = useAuth();
  const [activeCategory, setActiveCategory] = React.useState<'studio' | 'agents' | 'telephony'>('studio');

  const handleTalkToAgent = () => {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('stl:open-voice-chat'));
    }
  };

  const handleSignUpClick = () => {
    if (!user) {
      signIn();
    } else {
      onOpenDashboard();
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-white dark:bg-[#08090e] text-stone-900 dark:text-white transition-colors duration-300 font-sans overflow-x-hidden">
      {/* ─── Top Navbar ────────────────────────────────────────────── */}
      <LandingNavbar onOpenDashboard={onOpenDashboard} />

      {/* ─── Hero Section ────────────────────────────────────────── */}
      <section className="relative pt-16 pb-6 px-4 max-w-5xl mx-auto text-center">
        <h1 className="text-4xl sm:text-6xl md:text-7xl font-extrabold tracking-tight text-stone-900 dark:text-white leading-[1.1]">
          Bringing technology <br className="hidden sm:inline" />
          <span className="bg-gradient-to-r from-stone-900 via-stone-700 to-stone-500 dark:from-white dark:via-stone-200 dark:to-stone-400 bg-clip-text text-transparent">
            to life
          </span>
        </h1>

        <p className="mt-5 text-sm sm:text-base md:text-lg text-stone-600 dark:text-stone-400 max-w-2xl mx-auto leading-relaxed">
          Powering the best enterprises, creators, and developers. From autonomous Voice Agents for customer experience, Creative Voice Studio for real-time synthesis, to carrier-grade telephony.
        </p>

        {/* Hero CTA Buttons */}
        <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
          <button
            onClick={handleTalkToAgent}
            type="button"
            className="px-8 py-3.5 rounded-full bg-indigo-600 text-white text-sm font-bold shadow-xl shadow-indigo-600/20 hover:bg-indigo-700 hover:scale-105 active:scale-95 transition-all cursor-pointer flex items-center gap-2"
          >
            <Headphones className="w-4 h-4" />
            <span>Talk to an agent</span>
          </button>
          <button
            onClick={handleSignUpClick}
            type="button"
            className="px-6 py-3.5 rounded-full border border-stone-300 dark:border-white/15 bg-white dark:bg-stone-900 text-stone-800 dark:text-stone-200 text-sm font-semibold hover:border-stone-400 transition-all cursor-pointer flex items-center gap-2"
          >
            <span>{user ? 'Go to Console' : 'Sign in with Google'}</span>
            <ArrowRight className="w-4 h-4" />
          </button>
        </div>
      </section>

      {/* ─── 3D Multi-Colored Voice Sphere Carousel ─────────────────── */}
      <section id="specialists" className="px-4">
        <VoiceSphereCarousel
          activeCategory={activeCategory}
          onSelectCategory={setActiveCategory}
        />
      </section>

      {/* ─── Voice agents for every industry ────────────────────────── */}
      <Reveal><IndustryShowcase /></Reveal>

      {/* ─── The full stack for interactive intelligence ────────────── */}
      <Reveal><ProductStack /></Reveal>

      {/* ─── Voice agents as fast as the models (node graph) ────────── */}
      <Reveal><AgentArchitecture /></Reveal>

      {/* ─── Capability tabs (real production telemetry) ────────────── */}
      <Reveal><CapabilityTabs /></Reveal>

      {/* ─── Multilingual rotating globe ────────────────────────────── */}
      <Reveal><MultilingualGlobe /></Reveal>

      {/* ─── Enterprise security ────────────────────────────────────── */}
      <Reveal><SecuritySection /></Reveal>

      {/* ─── Multi-Functional Studio (TTS / Multi-Agent / Telephony) ── */}
      <Reveal>
        <section id="studio" className="px-4 border-t border-stone-200/80 dark:border-white/5 pt-8">
          <CreativeVoiceStudio
            activeCategory={activeCategory}
            onSelectCategory={setActiveCategory}
            onOpenConsole={onOpenDashboard}
          />
        </section>
      </Reveal>

      {/* ─── Trust Bar (logo wall) ─────────────────────────────────── */}
      <EnterpriseTrustBar />

      {/* ─── Floating Voice Chat Pill (Bottom Right) ──────────────── */}
      <InteractiveVoiceChatWidget />

      {/* ─── Footer ────────────────────────────────────────────────── */}
      <LandingFooter />
    </div>
  );
}
