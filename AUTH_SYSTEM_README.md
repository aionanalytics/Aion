# AION Analytics - Authentication & Billing System

## Overview

This is a complete production-grade authentication and subscription billing system for AION Analytics. The system includes:

- User authentication with JWT tokens
- Subscription management with Stripe integration
- Admin authentication and management
- Payment failure handling
- Route protection middleware
- Frontend authentication pages

## Status

**Backend: ✅ Complete**
**Frontend: ⚠️ Basic Implementation (Requires Enhancement)**

### Completed Components

#### Backend (100%)
- ✅ PostgreSQL database models
- ✅ SQLAlchemy ORM with migrations
- ✅ User authentication service (signup, login, token management)
- ✅ Subscription management service
- ✅ Admin authentication service
- ✅ Stripe payment integration
- ✅ Webhook handlers for payment events
- ✅ JWT token generation and validation
- ✅ Password hashing with bcrypt (12 rounds)
- ✅ Account lockout (5 attempts → 15 min)
- ✅ API endpoints for all auth operations
- ✅ Authentication middleware

#### Frontend (40%)
- ✅ Login page
- ✅ Signup page with subscription selection
- ✅ Password reset page
- ✅ Payment error page
- ✅ Legal disclosure page
- ✅ Troubleshooting/FAQ page
- ⚠️ Missing: Auth context, hooks, middleware
- ⚠️ Missing: Stripe client integration
- ⚠️ Missing: Token storage/encryption
- ⚠️ Missing: Route protection

## Configuration

### Environment Variables

Add these to your `.env` file:

```bash
# Database
DATABASE_URL="postgresql://aion:aion@localhost:5432/aion_db"

# JWT Secret (generate with: python -c "import secrets; print(secrets.token_urlsafe(32))")
JWT_SECRET_KEY="your-secret-key-here"

# Stripe
STRIPE_SECRET_KEY="sk_test_your_stripe_secret_key"
STRIPE_PUBLISHABLE_KEY="pk_test_your_stripe_publishable_key"
STRIPE_WEBHOOK_SECRET="whsec_your_webhook_secret"

# Admin
ADMIN_PASSWORD="your-admin-password"

# Enable Auth (0=disabled, 1=enabled)
AUTH_ENABLED=0
```

### Database Setup

1. Install PostgreSQL:
```bash
# macOS
brew install postgresql@15
brew services start postgresql@15

# Ubuntu
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
```

2. Create database:
```bash
sudo -u postgres psql
CREATE DATABASE aion_db;
CREATE USER aion WITH PASSWORD 'aion';
GRANT ALL PRIVILEGES ON DATABASE aion_db TO aion;
\q
```

3. Run migrations:
```bash
# The database will auto-initialize on first startup
# Or manually run the migration:
psql -U aion -d aion_db -f backend/database/migrations/001_auth_schema.sql
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

## API Endpoints

### Authentication (`/api/auth/`)

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/signup` | POST | Create account + subscription | No |
| `/login` | POST | Login with email/password | No |
| `/verify` | POST | Verify JWT token | No |
| `/refresh` | POST | Refresh expired token | No |
| `/logout` | POST | Revoke token | No |
| `/password-reset` | POST | Request password reset | No |

### Subscription (`/api/subscription/`)

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/status` | GET | Get subscription details | Yes (User) |
| `/update-payment` | PUT | Update payment method | Yes (User) |
| `/upgrade` | POST | Change subscription plan | Yes (User) |
| `/cancel` | POST | Cancel subscription | Yes (User) |
| `/pricing` | GET | Get pricing info | No |

### Admin (`/api/admin/`)

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/login` | POST | Admin login (password only) | No |
| `/users` | GET | List all users | Yes (Admin) |
| `/users/{id}` | GET | Get user details | Yes (Admin) |
| `/subscriptions` | GET | List all subscriptions | Yes (Admin) |
| `/subscriptions/{id}` | GET | Get subscription details | Yes (Admin) |
| `/stats` | GET | System statistics | Yes (Admin) |

### Webhooks (`/api/webhooks/`)

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/stripe` | POST | Stripe webhook events | Signature |

## Pricing Model

### Base Subscriptions

| Plan | Monthly | Annual | Description |
|------|---------|--------|-------------|
| Swing | $199 | $1,990 | Swing trading bot |
| Day | $249 | $2,490 | Day trading bot |
| Both | $398 | $3,980 | Both bots (20% discount) |

### Add-ons

| Add-on | Monthly | Annual |
|--------|---------|--------|
| Advanced Analytics | +$49 | +$490 |
| Cloud Backup | +$29 | +$290 |

### Early Adopter Discount

- First 100 signups: **-$50/month lifetime**
- Applied to all plans (monthly or annual)
- Non-transferable

## Usage Examples

### User Signup

```bash
curl -X POST http://localhost:8000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "securepassword123",
    "subscription_type": "both",
    "addons": ["analytics"],
    "billing_frequency": "monthly",
    "early_adopter": true,
    "payment_method_id": "pm_xxxxxxxxxxxxx"
  }'
```

Response:
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

### User Login

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type": application/json" \
  -d '{
    "email": "user@example.com",
    "password": "securepassword123"
  }'
```

### Admin Login

```bash
curl -X POST http://localhost:8000/api/admin/login \
  -H "Content-Type: application/json" \
  -d '{
    "password": "admin-password"
  }'
```

### Verify Token

```bash
curl -X POST http://localhost:8000/api/auth/verify \
  -H "Content-Type: application/json" \
  -d '{
    "token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
  }'
```

### Get Subscription Status

```bash
curl -X GET http://localhost:8000/api/subscription/status \
  -H "Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGc..."
```

## Security Features

✅ **Password Security**
- Bcrypt hashing with 12 rounds
- Minimum 8 character passwords
- No plain text storage

✅ **Token Security**
- JWT tokens with HS256 signing
- 24-hour access token expiry
- 30-day refresh token expiry
- Token revocation on logout
- Token hash storage in database

✅ **Account Protection**
- Rate limiting (5 failed attempts)
- 15-minute account lockout
- Secure password reset flow

✅ **Payment Security**
- Stripe PCI compliance
- No credit card storage
- Webhook signature verification

✅ **Middleware Protection**
- JWT validation on protected routes
- Admin-only route enforcement
- Payment status validation

## Architecture

### Database Models

```
users
├── id (UUID, PK)
├── email (VARCHAR, UNIQUE)
├── password_hash (VARCHAR)
├── created_at (TIMESTAMP)
├── updated_at (TIMESTAMP)
└── deleted_at (TIMESTAMP) -- soft delete

subscriptions
├── id (UUID, PK)
├── user_id (UUID, FK -> users.id)
├── subscription_type (VARCHAR) -- swing/day/both
├── addons (TEXT[])
├── billing_frequency (VARCHAR) -- monthly/annual
├── stripe_customer_id (VARCHAR)
├── stripe_subscription_id (VARCHAR)
├── status (VARCHAR) -- active/past_due/canceled/suspended
├── current_period_start (TIMESTAMP)
├── current_period_end (TIMESTAMP)
├── cancel_at_period_end (BOOLEAN)
├── early_adopter_discount (BOOLEAN)
├── created_at (TIMESTAMP)
└── updated_at (TIMESTAMP)

tokens
├── id (UUID, PK)
├── user_id (UUID, FK -> users.id)
├── token_hash (VARCHAR)
├── refresh_token_hash (VARCHAR)
├── expires_at (TIMESTAMP)
├── revoked (BOOLEAN)
└── created_at (TIMESTAMP)

admin_tokens
├── id (UUID, PK)
├── token_hash (VARCHAR)
├── expires_at (TIMESTAMP)
├── revoked (BOOLEAN)
└── created_at (TIMESTAMP)

password_resets
├── id (UUID, PK)
├── user_id (UUID, FK -> users.id)
├── token_hash (VARCHAR)
├── expires_at (TIMESTAMP)
├── used_at (TIMESTAMP)
└── created_at (TIMESTAMP)
```

### Service Layer

```
backend/
├── core/
│   ├── auth_service.py          # User auth logic
│   ├── subscription_service.py  # Subscription management
│   ├── admin_service.py         # Admin auth
│   └── stripe_service.py        # Stripe integration
├── models/
│   ├── user.py                  # User models
│   ├── subscription.py          # Subscription models
│   └── token.py                 # Token models
├── routers/
│   ├── auth_router.py           # Auth endpoints
│   ├── subscription_router.py   # Subscription endpoints
│   ├── admin_router_auth.py     # Admin endpoints
│   └── webhook_router.py        # Stripe webhooks
├── middleware/
│   └── auth_middleware.py       # JWT validation
└── database/
    ├── connection.py            # DB connection
    └── migrations/
        └── 001_auth_schema.sql  # Initial schema
```

## Frontend Integration (TODO)

The following frontend components need to be implemented:

### 1. Auth Manager (`lib/auth-manager.ts`)
```typescript
// Token storage and encryption
export class AuthManager {
  storeToken(token: string): void
  getToken(): string | null
  clearToken(): void
  encryptToken(token: string): string
  decryptToken(encrypted: string): string
}
```

### 2. Auth Context (`lib/auth-context.tsx`)
```typescript
// React context for auth state
export const AuthContext = createContext({
  user: null,
  token: null,
  login: async (email, password) => {},
  logout: () => {},
  isAuthenticated: false,
})
```

### 3. Auth Hooks (`hooks/useAuth.ts`)
```typescript
export function useAuth() {
  const context = useContext(AuthContext)
  return context
}

export function useProtectedRoute() {
  const { isAuthenticated } = useAuth()
  // Redirect to login if not authenticated
}
```

### 4. Route Protection Middleware (`middleware.ts`)
```typescript
export function middleware(request: NextRequest) {
  // Check JWT token
  // Redirect to /auth/login if invalid
  // Allow access to public routes
}
```

### 5. Stripe Client (`lib/stripe-client.ts`)
```typescript
import { loadStripe } from '@stripe/stripe-js'

export const stripePromise = loadStripe(
  process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY!
)
```

## Testing

### Unit Tests (TODO)

```bash
# Test auth endpoints
pytest tests/test_auth_endpoints.py

# Test subscription logic
pytest tests/test_subscription_service.py

# Test Stripe integration
pytest tests/test_stripe_service.py
```

### Integration Tests (TODO)

```bash
# End-to-end signup flow
pytest tests/integration/test_signup_flow.py

# Payment failure handling
pytest tests/integration/test_payment_failure.py
```

## Deployment

### 1. Database

```bash
# Production PostgreSQL
# Use managed service (AWS RDS, Google Cloud SQL, etc.)
# Enable SSL connections
# Regular backups
```

### 2. Backend

```bash
# Set production environment variables
export AUTH_ENABLED=1
export DATABASE_URL="postgresql://..."
export JWT_SECRET_KEY="..."
export STRIPE_SECRET_KEY="sk_live_..."

# Run with gunicorn (production)
gunicorn -w 4 -k uvicorn.workers.UvicornWorker backend.backend_service:app
```

### 3. Stripe Webhooks

```bash
# Configure webhook URL in Stripe dashboard
https://yourdomain.com/api/webhooks/stripe

# Events to subscribe:
- invoice.payment_failed
- invoice.payment_succeeded
- customer.subscription.deleted
- customer.subscription.updated
```

## Security Notes

### ⚠️ Frontend Token Storage

**Current Implementation**: The frontend auth pages store JWT tokens in `localStorage`. This is **NOT production-ready** and is vulnerable to XSS attacks.

**Production Recommendations**:

1. **Use HttpOnly Cookies** (Recommended):
   ```typescript
   // Backend sends token in httpOnly cookie
   response.set_cookie(
       key="access_token",
       value=token,
       httponly=True,
       secure=True,
       samesite="strict"
   )
   ```

2. **Token Encryption** (Alternative):
   - Encrypt tokens before storing in localStorage
   - Use AES-256-GCM encryption
   - Store encryption key securely (not in browser)

3. **Content Security Policy**:
   ```html
   <meta http-equiv="Content-Security-Policy"
         content="default-src 'self'; script-src 'self'">
   ```

4. **Additional Security Layers**:
   - Implement token refresh rotation
   - Use short-lived access tokens (15 minutes)
   - Implement device fingerprinting
   - Add CSRF tokens for state-changing operations

### Admin Password Security

**IMPORTANT**: The system now **only** supports hashed admin passwords via `ADMIN_PASSWORD_HASH`. Never store plain text passwords in environment variables.

Generate admin password hash:
```bash
python -c "import hashlib; print(hashlib.sha256(b'YOUR_PASSWORD').hexdigest())"
```

## Troubleshooting

### "Database connection failed"
- Check DATABASE_URL is correct
- Verify PostgreSQL is running
- Check database exists and user has permissions

### "Stripe API error"
- Verify STRIPE_SECRET_KEY is set
- Check Stripe dashboard for issues
- Ensure webhook secret is correct

### "Token invalid"
- Token may be expired (24 hours)
- Use refresh token to get new access token
- Check JWT_SECRET_KEY hasn't changed

### "Payment failed"
- Check Stripe customer portal for issues
- Verify payment method is valid
- Check for sufficient funds

## Support

For issues or questions:
- Email: support@aionanalytics.com
- Documentation: /legal/troubleshooting

## License

Proprietary - AION Analytics © 2026
