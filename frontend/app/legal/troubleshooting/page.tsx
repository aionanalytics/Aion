export default function TroubleshootingPage() {
  return (
    <div className="max-w-4xl mx-auto py-12 px-6">
      <h1 className="text-4xl font-bold text-white mb-8">FAQ & Troubleshooting</h1>
      
      <div className="space-y-8">
        <section>
          <h2 className="text-2xl font-bold text-white mb-4">Frequently Asked Questions</h2>
          
          <div className="space-y-6">
            <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
              <h3 className="text-lg font-semibold text-white mb-2">How do I sign up?</h3>
              <p className="text-gray-300">
                Click "Sign Up" on the homepage, choose your bot suite (Swing, Day, or Both), 
                select optional add-ons, and complete payment setup.
              </p>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
              <h3 className="text-lg font-semibold text-white mb-2">What payment methods do you accept?</h3>
              <p className="text-gray-300">
                We accept all major credit cards via Stripe. Your payment information is securely 
                processed and never stored on our servers.
              </p>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
              <h3 className="text-lg font-semibold text-white mb-2">Can I change my subscription plan?</h3>
              <p className="text-gray-300">
                Yes! You can upgrade or downgrade your plan at any time from your account settings. 
                Changes take effect immediately with prorated billing.
              </p>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
              <h3 className="text-lg font-semibold text-white mb-2">What happens if my payment fails?</h3>
              <p className="text-gray-300">
                If your payment fails, you'll receive an email notification. You'll have 7 days 
                to update your payment method before your account is suspended. Login will be 
                blocked until payment is updated.
              </p>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
              <h3 className="text-lg font-semibold text-white mb-2">How do I cancel my subscription?</h3>
              <p className="text-gray-300">
                You can cancel anytime from your account settings. You'll retain access until 
                the end of your billing period. No refunds for partial months.
              </p>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
              <h3 className="text-lg font-semibold text-white mb-2">What is the early adopter discount?</h3>
              <p className="text-gray-300">
                The first 100 users get a lifetime $50/month discount on all plans. Once you 
                have it, it stays with your account forever!
              </p>
            </div>
          </div>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">Common Issues</h2>
          
          <div className="space-y-4">
            <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
              <h3 className="text-lg font-semibold text-white mb-2">I forgot my password</h3>
              <p className="text-gray-300 mb-3">
                Click "Forgot password?" on the login page. You'll receive a password reset 
                link via email (check spam folder if you don't see it).
              </p>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
              <h3 className="text-lg font-semibold text-white mb-2">My account is locked</h3>
              <p className="text-gray-300 mb-3">
                After 5 failed login attempts, your account is locked for 15 minutes for 
                security. Wait 15 minutes and try again.
              </p>
            </div>

            <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
              <h3 className="text-lg font-semibold text-white mb-2">I can't login after payment</h3>
              <p className="text-gray-300 mb-3">
                Make sure your payment went through successfully. Check your email for a 
                confirmation. If payment failed, update your payment method.
              </p>
            </div>
          </div>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">Contact Support</h2>
          <div className="bg-gray-800 p-6 rounded-lg border border-gray-700">
            <p className="text-gray-300 mb-4">
              Still need help? Reach out to our support team:
            </p>
            <ul className="space-y-2 text-gray-300">
              <li>📧 Email: support@aionanalytics.com</li>
              <li>🕐 Response time: Within 24 hours</li>
              <li>📚 Documentation: Check our knowledge base for detailed guides</li>
            </ul>
          </div>
        </section>
      </div>
    </div>
  );
}
