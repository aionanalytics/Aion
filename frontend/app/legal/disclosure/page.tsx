export default function DisclosurePage() {
  return (
    <div className="max-w-4xl mx-auto py-12 px-6">
      <h1 className="text-4xl font-bold text-white mb-8">Legal Disclosure</h1>
      
      <div className="space-y-8 text-gray-300">
        <section>
          <h2 className="text-2xl font-bold text-white mb-4">Terms of Service</h2>
          <p className="mb-4">
            By using AION Analytics, you agree to our terms of service. This is an automated trading platform
            that uses machine learning algorithms to make trading decisions.
          </p>
          <ul className="list-disc list-inside space-y-2 ml-4">
            <li>You must be 18 years or older to use this service</li>
            <li>You are responsible for maintaining the confidentiality of your account</li>
            <li>You agree to comply with all applicable laws and regulations</li>
            <li>We reserve the right to terminate accounts that violate our terms</li>
          </ul>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">Privacy Policy</h2>
          <p className="mb-4">
            We take your privacy seriously. Here's how we handle your data:
          </p>
          <ul className="list-disc list-inside space-y-2 ml-4">
            <li>We collect email, password, and payment information</li>
            <li>We use cookies to maintain your session</li>
            <li>We never sell your personal information</li>
            <li>Your trading data is encrypted and stored securely</li>
            <li>You can request data deletion at any time</li>
          </ul>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">Risk Disclaimer</h2>
          <div className="bg-red-900/20 border border-red-700 p-6 rounded-lg">
            <p className="font-bold text-red-400 mb-3">⚠️ IMPORTANT RISK WARNING</p>
            <p className="mb-4">
              Trading stocks and other securities involves substantial risk and is not suitable for all investors.
              You may lose all or part of your investment.
            </p>
            <ul className="list-disc list-inside space-y-2 ml-4 text-sm">
              <li>Past performance does not guarantee future results</li>
              <li>AI/ML algorithms can make mistakes and lose money</li>
              <li>Market conditions can change rapidly</li>
              <li>You are solely responsible for your trading decisions</li>
              <li>We are not financial advisors and do not provide investment advice</li>
              <li>Always trade with money you can afford to lose</li>
              <li>Consider consulting with a licensed financial advisor</li>
            </ul>
          </div>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">Billing & Refunds</h2>
          <ul className="list-disc list-inside space-y-2 ml-4">
            <li>Subscriptions are billed monthly or annually based on your selection</li>
            <li>You can cancel your subscription at any time</li>
            <li>Refunds are issued on a case-by-case basis</li>
            <li>Early adopter discounts are lifetime and non-transferable</li>
            <li>Payment failures may result in service suspension</li>
          </ul>
        </section>

        <section className="text-sm text-gray-500">
          <p>Last updated: January 2026</p>
          <p className="mt-2">
            For questions about these terms, contact us at legal@aionanalytics.com
          </p>
        </section>
      </div>
    </div>
  );
}
