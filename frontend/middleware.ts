import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

// Public routes that don't require authentication
const PUBLIC_ROUTES = [
  '/auth/login',
  '/auth/signup',
  '/auth/password-reset',
  '/auth/payment-error',
];

// Admin routes that require admin authentication
const ADMIN_ROUTES = [
  '/tools/admin',
];

// API routes that should not be protected by frontend middleware
const API_ROUTES = [
  '/api/',
];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  
  // Skip API routes - let backend handle authentication
  if (API_ROUTES.some(route => pathname.startsWith(route))) {
    return NextResponse.next();
  }
  
  // Skip static files and Next.js internals
  if (
    pathname.startsWith('/_next') ||
    pathname.startsWith('/static') ||
    pathname.match(/\.(ico|png|jpg|jpeg|gif|svg|woff|woff2|ttf|eot|css|js|map)$/i)
  ) {
    return NextResponse.next();
  }
  
  // Allow public routes
  if (PUBLIC_ROUTES.includes(pathname)) {
    return NextResponse.next();
  }
  
  // Check for authentication token in cookies
  const accessToken = request.cookies.get('access_token');
  const adminToken = request.cookies.get('admin_token');
  
  // Helper function to check if a token looks valid (basic format check)
  const isValidTokenFormat = (token: any) => {
    return token && token.value && typeof token.value === 'string' && token.value.length > 10;
  };
  
  // Admin routes require admin token
  if (ADMIN_ROUTES.some(route => pathname.startsWith(route))) {
    if (!isValidTokenFormat(adminToken)) {
      // Redirect to login with admin flag
      const loginUrl = new URL('/auth/login', request.url);
      return NextResponse.redirect(loginUrl);
    }
    return NextResponse.next();
  }
  
  // All other routes require user authentication
  if (!isValidTokenFormat(accessToken)) {
    // Redirect to login
    const loginUrl = new URL('/auth/login', request.url);
    return NextResponse.redirect(loginUrl);
  }
  
  return NextResponse.next();
}

// Configure which routes to run middleware on
export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - api (API routes)
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     */
    '/((?!api|_next/static|_next/image|favicon.ico).*)',
  ],
};
