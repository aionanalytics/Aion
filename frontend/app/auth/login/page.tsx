"use client";

import { useState } from 'react';
import { useAuth } from '@/lib/auth-context';

export default function LoginPage() {
  const { login, adminLogin } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showAdminLogin, setShowAdminLogin] = useState(false);
  const [adminPassword, setAdminPassword] = useState('');

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  const handleAdminLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await adminLogin(adminPassword);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Admin login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-900">
      <div className="bg-gray-800 p-8 rounded-lg border border-gray-700 w-full max-w-md">
        <h1 className="text-3xl font-bold text-white mb-6">AION Analytics</h1>
        <p className="text-gray-400 mb-6">Sign in to your account</p>
        
        {error && (
          <div className="bg-red-900/50 border border-red-700 text-red-400 p-3 rounded mb-4">
            {error}
          </div>
        )}
        
        {!showAdminLogin ? (
          <>
            <form onSubmit={handleLogin} className="space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-300 mb-2">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="you@example.com"
                />
              </div>
              
              <div>
                <label htmlFor="password" className="block text-sm font-medium text-gray-300 mb-2">
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="••••••••"
                />
              </div>
              
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 text-white font-semibold rounded-lg transition-colors"
              >
                {loading ? 'Signing in...' : 'Sign In'}
              </button>
            </form>
            
            <div className="mt-6 text-center space-y-2">
              <a href="/auth/password-reset" className="block text-sm text-blue-400 hover:text-blue-300">
                Forgot password?
              </a>
              <a href="/auth/signup" className="block text-sm text-gray-400">
                Don't have an account? <span className="text-blue-400 hover:text-blue-300">Sign up</span>
              </a>
              <div className="pt-4 border-t border-gray-700 mt-4">
                <button
                  onClick={() => setShowAdminLogin(true)}
                  className="text-sm text-gray-400 hover:text-gray-300 transition-colors"
                >
                  Admin Login →
                </button>
              </div>
            </div>
          </>
        ) : (
          <>
            <form onSubmit={handleAdminLogin} className="space-y-4">
              <div>
                <label htmlFor="admin-password" className="block text-sm font-medium text-gray-300 mb-2">
                  Admin Password
                </label>
                <input
                  id="admin-password"
                  type="password"
                  value={adminPassword}
                  onChange={(e) => setAdminPassword(e.target.value)}
                  required
                  className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                  placeholder="••••••••"
                />
              </div>
              
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-purple-800 text-white font-semibold rounded-lg transition-colors"
              >
                {loading ? 'Signing in...' : 'Admin Sign In'}
              </button>
            </form>
            
            <div className="mt-6 text-center">
              <button
                onClick={() => {
                  setShowAdminLogin(false);
                  setAdminPassword('');
                  setError('');
                }}
                className="text-sm text-gray-400 hover:text-gray-300 transition-colors"
              >
                ← Back to User Login
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
