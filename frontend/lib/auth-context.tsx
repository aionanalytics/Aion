"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { useRouter, usePathname } from 'next/navigation';

interface User {
  id: string;
  email: string;
  subscription_status?: string;
}

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  isAdmin: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (data: SignupData) => Promise<void>;
  adminLogin: (password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshAuth: () => Promise<void>;
}

interface SignupData {
  email: string;
  password: string;
  subscription_type: string;
  addons: string[];
  billing_frequency: string;
  early_adopter?: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// Public routes that don't require authentication
const PUBLIC_ROUTES = [
  '/auth/login',
  '/auth/signup',
  '/auth/password-reset',
  '/auth/payment-error',
];

// Admin routes
const ADMIN_ROUTES = [
  '/tools/admin',
];

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  // Verify authentication on mount and route changes
  useEffect(() => {
    refreshAuth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  const refreshAuth = async () => {
    try {
      setIsLoading(true);
      
      // Try to verify existing token (from cookie or localStorage)
      const response = await fetch('/api/backend/auth/verify', {
        method: 'POST',
        credentials: 'include', // Include cookies
      });

      if (response.ok) {
        const data = await response.json();
        if (data.valid && data.user_id) {
          setUser({
            id: data.user_id,
            email: data.email || '',
            subscription_status: data.subscription_status,
          });
          setIsAdmin(false);
        } else {
          // Invalid token, clear state
          setUser(null);
          setIsAdmin(false);
          
          // Redirect to login if on protected route
          if (!PUBLIC_ROUTES.includes(pathname)) {
            router.push('/auth/login');
          }
        }
      } else {
        // No valid auth, clear state
        setUser(null);
        setIsAdmin(false);
        
        // Redirect to login if on protected route
        if (!PUBLIC_ROUTES.includes(pathname)) {
          router.push('/auth/login');
        }
      }
    } catch (error) {
      console.error('Auth verification failed:', error);
      setUser(null);
      setIsAdmin(false);
      
      // Redirect to login if on protected route
      if (!PUBLIC_ROUTES.includes(pathname)) {
        router.push('/auth/login');
      }
    } finally {
      setIsLoading(false);
    }
  };

  const login = async (email: string, password: string) => {
    try {
      const response = await fetch('/api/backend/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
        credentials: 'include', // Include cookies
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail?.error || data.detail || 'Login failed');
      }

      const data = await response.json();
      
      // Store tokens in localStorage as fallback
      if (data.access_token) {
        localStorage.setItem('access_token', data.access_token);
      }
      if (data.refresh_token) {
        localStorage.setItem('refresh_token', data.refresh_token);
      }
      
      // Refresh auth state
      await refreshAuth();
      
      // Redirect to dashboard
      router.push('/dashboard');
    } catch (error) {
      throw error;
    }
  };

  const signup = async (signupData: SignupData) => {
    try {
      const response = await fetch('/api/backend/auth/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: signupData.email,
          password: signupData.password,
          subscription_type: signupData.subscription_type,
          addons: signupData.addons,
          billing_frequency: signupData.billing_frequency,
          early_adopter: signupData.early_adopter || true,
        }),
        credentials: 'include', // Include cookies
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail?.error || data.detail || 'Signup failed');
      }

      const data = await response.json();
      
      // Store tokens in localStorage as fallback
      if (data.access_token) {
        localStorage.setItem('access_token', data.access_token);
      }
      if (data.refresh_token) {
        localStorage.setItem('refresh_token', data.refresh_token);
      }
      
      // Refresh auth state
      await refreshAuth();
      
      // Redirect to dashboard
      router.push('/dashboard');
    } catch (error) {
      throw error;
    }
  };

  const adminLogin = async (password: string) => {
    try {
      const response = await fetch('/api/backend/auth/admin-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
        credentials: 'include', // Include cookies
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail?.error || data.detail || 'Admin login failed');
      }

      const data = await response.json();
      
      // Store admin token in localStorage as fallback
      if (data.access_token) {
        localStorage.setItem('admin_token', data.access_token);
      }
      
      setIsAdmin(true);
      
      // Redirect to admin tools
      router.push('/tools/admin');
    } catch (error) {
      throw error;
    }
  };

  const logout = async () => {
    try {
      await fetch('/api/backend/auth/logout', {
        method: 'POST',
        credentials: 'include', // Include cookies
      });
    } catch (error) {
      console.error('Logout error:', error);
    } finally {
      // Clear all stored tokens
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      localStorage.removeItem('admin_token');
      
      // Clear state
      setUser(null);
      setIsAdmin(false);
      
      // Redirect to login
      router.push('/auth/login');
    }
  };

  return (
    <AuthContext.Provider value={{ user, isLoading, isAdmin, login, signup, adminLogin, logout, refreshAuth }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
