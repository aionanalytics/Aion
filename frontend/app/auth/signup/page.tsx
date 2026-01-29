"use client";

import { useState } from 'react';
import { useAuth } from '@/lib/auth-context';

export default function SignupPage() {
  const { signup } = useAuth();
  const [formData, setFormData] = useState({
    email: '',
    password: '',
    confirmPassword: '',
    subscriptionType: 'swing' as 'swing' | 'day' | 'both',
    addons: [] as string[],
    billingFrequency: 'monthly' as 'monthly' | 'annual',
  });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const pricing = {
    swing: { monthly: 199, annual: 1990 },
    day: { monthly: 249, annual: 2490 },
    both: { monthly: 398, annual: 3980 },
  };

  const addonPricing = {
    analytics: { monthly: 49, annual: 490 },
    backup: { monthly: 29, annual: 290 },
  };

  const calculateTotal = () => {
    const base = pricing[formData.subscriptionType][formData.billingFrequency];
    const addonsTotal = formData.addons.reduce((sum, addon) => {
      const price = addonPricing[addon as keyof typeof addonPricing]?.[formData.billingFrequency] || 0;
      return sum + price;
    }, 0);
    return base + addonsTotal;
  };

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (formData.password !== formData.confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    if (formData.password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    setLoading(true);

    try {
      await signup({
        email: formData.email,
        password: formData.password,
        subscription_type: formData.subscriptionType,
        addons: formData.addons,
        billing_frequency: formData.billingFrequency,
        early_adopter: true,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Signup failed');
    } finally {
      setLoading(false);
    }
  };

  const toggleAddon = (addon: string) => {
    setFormData(prev => ({
      ...prev,
      addons: prev.addons.includes(addon)
        ? prev.addons.filter(a => a !== addon)
        : [...prev.addons, addon]
    }));
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-900 py-12">
      <div className="bg-gray-800 p-8 rounded-lg border border-gray-700 w-full max-w-2xl">
        <h1 className="text-3xl font-bold text-white mb-2">Create Account</h1>
        <p className="text-gray-400 mb-6">Join AION Analytics</p>
        
        {error && (
          <div className="bg-red-900/50 border border-red-700 text-red-400 p-3 rounded mb-4">
            {error}
          </div>
        )}
        
        <form onSubmit={handleSignup} className="space-y-6">
          {/* Email & Password */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Email</label>
              <input
                type="email"
                value={formData.email}
                onChange={(e) => setFormData(prev => ({ ...prev, email: e.target.value }))}
                required
                className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-white"
                placeholder="you@example.com"
              />
            </div>
            
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Password</label>
              <input
                type="password"
                value={formData.password}
                onChange={(e) => setFormData(prev => ({ ...prev, password: e.target.value }))}
                required
                className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-white"
                placeholder="Minimum 8 characters"
              />
            </div>
            
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Confirm Password</label>
              <input
                type="password"
                value={formData.confirmPassword}
                onChange={(e) => setFormData(prev => ({ ...prev, confirmPassword: e.target.value }))}
                required
                className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-white"
              />
            </div>
          </div>

          {/* Subscription Type */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-3">Bot Suite</label>
            <div className="grid grid-cols-3 gap-3">
              {(['swing', 'day', 'both'] as const).map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => setFormData(prev => ({ ...prev, subscriptionType: type }))}
                  className={`p-4 rounded-lg border-2 transition-colors ${
                    formData.subscriptionType === type
                      ? 'border-blue-500 bg-blue-900/30'
                      : 'border-gray-700 bg-gray-900'
                  }`}
                >
                  <div className="text-white font-semibold capitalize">{type}</div>
                  <div className="text-sm text-gray-400 mt-1">
                    ${pricing[type].monthly}/mo
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Add-ons */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-3">Add-ons (Optional)</label>
            <div className="space-y-2">
              <label className="flex items-center p-3 bg-gray-900 rounded-lg cursor-pointer">
                <input
                  type="checkbox"
                  checked={formData.addons.includes('analytics')}
                  onChange={() => toggleAddon('analytics')}
                  className="mr-3"
                />
                <div className="flex-1">
                  <div className="text-white">Advanced Analytics</div>
                  <div className="text-sm text-gray-400">+$49/month</div>
                </div>
              </label>
              
              <label className="flex items-center p-3 bg-gray-900 rounded-lg cursor-pointer">
                <input
                  type="checkbox"
                  checked={formData.addons.includes('backup')}
                  onChange={() => toggleAddon('backup')}
                  className="mr-3"
                />
                <div className="flex-1">
                  <div className="text-white">Cloud Backup</div>
                  <div className="text-sm text-gray-400">+$29/month</div>
                </div>
              </label>
            </div>
          </div>

          {/* Billing Frequency */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-3">Billing</label>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setFormData(prev => ({ ...prev, billingFrequency: 'monthly' }))}
                className={`p-3 rounded-lg border-2 ${
                  formData.billingFrequency === 'monthly'
                    ? 'border-blue-500 bg-blue-900/30'
                    : 'border-gray-700 bg-gray-900'
                }`}
              >
                <div className="text-white font-semibold">Monthly</div>
              </button>
              
              <button
                type="button"
                onClick={() => setFormData(prev => ({ ...prev, billingFrequency: 'annual' }))}
                className={`p-3 rounded-lg border-2 ${
                  formData.billingFrequency === 'annual'
                    ? 'border-blue-500 bg-blue-900/30'
                    : 'border-gray-700 bg-gray-900'
                }`}
              >
                <div className="text-white font-semibold">Annual</div>
                <div className="text-sm text-green-400">Save ~15%</div>
              </button>
            </div>
          </div>

          {/* Total */}
          <div className="bg-gray-900 p-4 rounded-lg">
            <div className="flex justify-between text-lg">
              <span className="text-gray-300">Total:</span>
              <span className="text-white font-bold">${calculateTotal()}/{formData.billingFrequency === 'monthly' ? 'month' : 'year'}</span>
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 text-white font-semibold rounded-lg transition-colors"
          >
            {loading ? 'Creating Account...' : 'Create Account'}
          </button>
        </form>
        
        <div className="mt-6 text-center">
          <a href="/auth/login" className="text-sm text-gray-400">
            Already have an account? <span className="text-blue-400 hover:text-blue-300">Sign in</span>
          </a>
        </div>
      </div>
    </div>
  );
}
