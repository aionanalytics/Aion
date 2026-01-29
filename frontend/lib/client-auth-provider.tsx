"use client";

import { ReactNode } from 'react';
import { AuthProvider } from '@/lib/auth-context';

export function ClientAuthProvider({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}
