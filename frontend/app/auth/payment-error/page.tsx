"use client";

export default function PaymentErrorPage() {
  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-900">
      <div className="bg-gray-800 p-8 rounded-lg border border-red-700 w-full max-w-md text-center">
        <div className="text-red-400 text-4xl mb-4">⚠</div>
        <h1 className="text-2xl font-bold text-white mb-3">Payment Failed</h1>
        <p className="text-gray-400 mb-6">
          Your payment method has failed. Please update your payment information to continue using AION Analytics.
        </p>
        
        <div className="space-y-3">
          <button
            onClick={() => {
              // TODO: Integrate with Stripe customer portal
              window.location.href = '/profile'; // Redirect to profile/billing page
            }}
            className="w-full py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg transition-colors"
          >
            Update Payment Method
          </button>
          
          <a
            href="/legal/troubleshooting"
            className="block text-sm text-gray-400 hover:text-gray-300"
          >
            Need help? Contact support
          </a>
        </div>
      </div>
    </div>
  );
}
