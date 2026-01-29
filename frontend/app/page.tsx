"use client";

import { useEffect } from 'react';
import { useAuth } from '@/lib/auth-context';
import { useRouter } from 'next/navigation';
import SearchBar from "@/components/SearchBar";
import LogoHeader from "@/components/LogoHeader";
import AccuracyCard from "@/components/AccuracyCard";
import TopPredictions from "@/components/TopPredictions";

export default function Page() {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    // Redirect authenticated users to dashboard
    if (!isLoading && user) {
      router.push('/dashboard');
    }
  }, [user, isLoading, router]);

  // Show loading state while checking auth
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-gray-400">Loading...</div>
      </div>
    );
  }

  // Show home page for unauthenticated users
  return (
    <div className="space-y-6">
      <SearchBar />
      <LogoHeader />
      <section className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-4">
        <AccuracyCard />
        <TopPredictions title="Top 3 — 1 Week" horizon="1w" />
        <TopPredictions title="Top 3 — 1 Month" horizon="4w" />
      </section>
    </div>
  );
}
