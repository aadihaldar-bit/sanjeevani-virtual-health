'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Play, Pause, ChevronLeft, ChevronRight, ShieldCheck, Sparkles, Cpu, Radio } from 'lucide-react';
import {
  VoicePersona,
  VOICE_STUDIO_PERSONAS,
  VOICE_AGENTS_PERSONAS,
  TELEPHONY_API_PERSONAS,
  synthesizeSpeechLive
} from '@/lib/audioSamples';

interface VoiceSphereCarouselProps {
  activeCategory?: 'studio' | 'agents' | 'telephony';
  onSelectCategory?: (category: 'studio' | 'agents' | 'telephony') => void;
}

export function VoiceSphereCarousel({
  activeCategory = 'studio',
  onSelectCategory,
}: VoiceSphereCarouselProps) {
  const [internalCategory, setInternalCategory] = useState<'studio' | 'agents' | 'telephony'>('studio');
  const currentCategory = onSelectCategory ? activeCategory : internalCategory;

  const [currentIndex, setCurrentIndex] = useState(2);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const personas =
    currentCategory === 'agents'
      ? VOICE_AGENTS_PERSONAS
      : currentCategory === 'telephony'
      ? TELEPHONY_API_PERSONAS
      : VOICE_STUDIO_PERSONAS;

  const activePersona = personas[currentIndex] || personas[0];

  const handleCategoryChange = (cat: 'studio' | 'agents' | 'telephony') => {
    stopAudio();
    if (onSelectCategory) {
      onSelectCategory(cat);
    } else {
      setInternalCategory(cat);
    }
    setCurrentIndex(1);
  };

  const handlePrev = () => {
    stopAudio();
    setCurrentIndex(prev => (prev === 0 ? personas.length - 1 : prev - 1));
  };

  const handleNext = () => {
    stopAudio();
    setCurrentIndex(prev => (prev === personas.length - 1 ? 0 : prev + 1));
  };

  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    setPlayingId(null);
  };

  const handleTogglePlay = async (persona: VoicePersona) => {
    if (playingId === persona.id) {
      stopAudio();
      return;
    }

    stopAudio();
    setPlayingId(persona.id);

    const audio = await synthesizeSpeechLive(persona.sampleText, persona.voiceName.split(' ')[0]);
    if (audio) {
      audioRef.current = audio;
      audio.onended = () => setPlayingId(null);
      audio.onerror = () => setPlayingId(null);
      audio.play().catch(() => setPlayingId(null));
    }
  };

  useEffect(() => {
    return () => {
      stopAudio();
    };
  }, []);

  return (
    <div className="w-full max-w-5xl mx-auto my-8">
      {/* ─── Top Category Tabs (Authentic STL Suite Branding) ─────────────────────── */}
      <div className="flex items-center justify-center mb-6">
        <div className="inline-flex p-1 rounded-full border border-stone-200 dark:border-white/10 bg-white/90 dark:bg-stone-900/90 backdrop-blur-md shadow-sm">
          <button
            onClick={() => handleCategoryChange('studio')}
            className={`flex items-center gap-2 px-4 py-1.5 rounded-full text-xs font-semibold transition-all cursor-pointer ${
              currentCategory === 'studio'
                ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900 shadow-sm'
                : 'text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white'
            }`}
          >
            <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />
            STL Voice Studio
          </button>
          <button
            onClick={() => handleCategoryChange('agents')}
            className={`flex items-center gap-2 px-4 py-1.5 rounded-full text-xs font-semibold transition-all cursor-pointer ${
              currentCategory === 'agents'
                ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900 shadow-sm'
                : 'text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white'
            }`}
          >
            <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />
            STL Voice Agents
          </button>
          <button
            onClick={() => handleCategoryChange('telephony')}
            className={`flex items-center gap-2 px-4 py-1.5 rounded-full text-xs font-semibold transition-all cursor-pointer ${
              currentCategory === 'telephony'
                ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900 shadow-sm'
                : 'text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white'
            }`}
          >
            <span className="w-2 h-2 rounded-full bg-indigo-500" />
            STL Telephony &amp; API
          </button>
        </div>
      </div>

      {/* ─── Main 3D Spheres Carousel Box ─────────────────────────────────────────── */}
      <div className="relative overflow-hidden rounded-3xl border border-stone-200/80 dark:border-white/10 bg-gradient-to-b from-stone-50/80 to-white dark:from-[#0d0f17] dark:to-[#08090e] p-6 sm:p-10 shadow-xl backdrop-blur-xl">
        
        {/* Navigation Arrow Controls */}
        <button
          onClick={handlePrev}
          title="Previous Sphere"
          className="absolute left-3 sm:left-5 top-1/2 -translate-y-1/2 z-30 w-10 h-10 rounded-full border border-stone-200 dark:border-white/10 bg-white/95 dark:bg-stone-900/95 text-stone-700 dark:text-stone-300 flex items-center justify-center hover:scale-105 active:scale-95 transition-all shadow-md cursor-pointer"
        >
          <ChevronLeft className="w-5 h-5" />
        </button>
        <button
          onClick={handleNext}
          title="Next Sphere"
          className="absolute right-3 sm:right-5 top-1/2 -translate-y-1/2 z-30 w-10 h-10 rounded-full border border-stone-200 dark:border-white/10 bg-white/95 dark:bg-stone-900/95 text-stone-700 dark:text-stone-300 flex items-center justify-center hover:scale-105 active:scale-95 transition-all shadow-md cursor-pointer"
        >
          <ChevronRight className="w-5 h-5" />
        </button>

        {/* ─── 3D Sphere Row (Multi-colored vibrant spheres) ────────────────────────── */}
        <div className="flex items-center justify-center gap-4 sm:gap-6 md:gap-8 py-6 min-h-[260px] overflow-hidden">
          {personas.map((persona, idx) => {
            const isCenter = idx === currentIndex;
            const isPlaying = playingId === persona.id;

            return (
              <div
                key={persona.id}
                onClick={() => {
                  if (!isCenter) {
                    setCurrentIndex(idx);
                    stopAudio();
                  }
                }}
                className={`transition-all duration-500 flex flex-col items-center cursor-pointer select-none flex-shrink-0 ${
                  isCenter
                    ? 'scale-105 sm:scale-115 opacity-100 z-20'
                    : 'scale-70 sm:scale-80 opacity-50 hover:opacity-85'
                }`}
              >
                {/* 3D Sphere Orb Container */}
                <div className="relative group flex items-center justify-center">
                  
                  {/* Outer Pulsing Soundwave Rings */}
                  {isPlaying && (
                    <>
                      <div
                        className="absolute inset-0 rounded-full animate-ping opacity-40 pointer-events-none"
                        style={{ background: persona.glowColor }}
                      />
                      <div
                        className="absolute -inset-4 rounded-full animate-pulse opacity-50 blur-lg pointer-events-none"
                        style={{ background: persona.glowColor }}
                      />
                    </>
                  )}

                  {/* The Perfectly Spherical 3D Textured Orb */}
                  <div
                    className={`relative w-28 h-28 sm:w-36 sm:h-36 md:w-44 md:h-44 rounded-full flex-shrink-0 shadow-2xl flex items-center justify-center transition-transform duration-300 ${
                      isPlaying ? 'animate-spin-slow' : 'group-hover:scale-105'
                    }`}
                    style={{
                      background: persona.sphereGradient,
                      boxShadow: isCenter
                        ? `inset -12px -12px 28px rgba(0,0,0,0.6), inset 12px 12px 28px rgba(255,255,255,0.7), 0 20px 40px -10px ${persona.glowColor}`
                        : 'inset -8px -8px 18px rgba(0,0,0,0.5), inset 8px 8px 18px rgba(255,255,255,0.5), 0 10px 20px -5px rgba(0,0,0,0.2)',
                      aspectRatio: '1 / 1',
                    }}
                  >
                    {/* Soft Glass highlight reflection */}
                    <div className="absolute top-2 left-4 sm:top-3 sm:left-6 w-8 h-8 sm:w-12 sm:h-12 rounded-full bg-white/40 blur-[3px] pointer-events-none" />

                    {/* Central Play/Pause Button (Only on Center Sphere) */}
                    {isCenter && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleTogglePlay(persona);
                        }}
                        type="button"
                        title={isPlaying ? 'Pause' : 'Play'}
                        className="w-12 h-12 sm:w-14 sm:h-14 rounded-full bg-white text-stone-900 dark:bg-stone-950 dark:text-white flex items-center justify-center shadow-2xl hover:scale-110 active:scale-95 transition-all z-20 cursor-pointer"
                      >
                        {isPlaying ? (
                          <Pause className="w-5 h-5 sm:w-6 sm:h-6 fill-current" />
                        ) : (
                          <Play className="w-5 h-5 sm:w-6 sm:h-6 fill-current ml-1" />
                        )}
                      </button>
                    )}
                  </div>
                </div>

                {/* Sphere Title & Subtext */}
                <div className="text-center mt-5">
                  <h4 className={`text-sm sm:text-base font-bold tracking-tight ${isCenter ? 'text-stone-900 dark:text-white' : 'text-stone-500'}`}>
                    {persona.name}
                  </h4>
                  {isCenter && (
                    <p className="text-xs text-stone-500 dark:text-stone-400 mt-1 max-w-[220px] line-clamp-2">
                      {persona.description}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* ─── Active Persona Quote & Telemetry Bar ──────────────────────────────────── */}
        <div className="mt-4 pt-4 border-t border-stone-200 dark:border-white/10 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-stone-600 dark:text-stone-300">
          <div className="flex items-center gap-2">
            <span className="px-2.5 py-1 rounded-full bg-stone-100 dark:bg-stone-800 font-mono text-[11px] font-semibold">
              Voice: {activePersona.voiceName}
            </span>
            <span className="hidden sm:inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium">
              <ShieldCheck className="w-3.5 h-3.5" /> SOP Grounded
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            {activePersona.features.map(f => (
              <span
                key={f}
                className="px-2 py-0.5 rounded-md text-[10px] font-medium bg-stone-100 dark:bg-white/5 text-stone-700 dark:text-stone-300 border border-stone-200 dark:border-white/5"
              >
                {f}
              </span>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}
