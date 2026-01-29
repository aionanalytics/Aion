/**
 * API Helper Utilities
 * 
 * Provides validation and helper functions to ensure proper API usage.
 * Helps prevent common mistakes like:
 * - Hardcoded backend URLs (localhost:8000, IP addresses)
 * - Double /api prefixes
 * - Direct backend calls instead of proxy routes
 * 
 * Usage:
 * ```typescript
 * import { validateApiUrl, getProxyUrl } from "@/lib/apiHelper";
 * 
 * // Validate URL before fetch
 * const url = validateApiUrl("/api/backend/bots/page");
 * const response = await fetch(url);
 * 
 * // Or use helper to construct URL
 * const url = getProxyUrl("/bots/page");
 * const response = await fetch(url);
 * ```
 */

/**
 * Patterns that indicate incorrect API usage
 */
const INVALID_PATTERNS = {
  HARDCODED_LOCALHOST: /localhost:\d+/,
  HARDCODED_IP: /\d+\.\d+\.\d+\.\d+:\d+/,
  DOUBLE_API_PREFIX: /\/api\/backend\/api\//,
  DIRECT_HTTP_URL: /^https?:\/\//,
} as const;

/**
 * Validate API URL and warn if it looks incorrect.
 * 
 * This function checks for common mistakes:
 * - Hardcoded localhost URLs
 * - Hardcoded IP addresses
 * - Double /api prefixes
 * - Direct HTTP URLs (should use proxy)
 * 
 * @param url - The URL to validate
 * @param context - Optional context for better error messages (e.g., component name)
 * @param strict - If true, throws errors for all violations. If false, only warns. Default: true in dev, false in prod
 * @returns The validated URL (unchanged if valid)
 * @throws Error if strict=true and URL is invalid
 */
export function validateApiUrl(url: string, context?: string, strict?: boolean): string {
  // Determine strictness: true in dev by default, false in prod
  const isStrict = strict !== undefined ? strict : (process.env.NODE_ENV === "development");
  
  // Skip validation in production unless explicitly strict
  if (process.env.NODE_ENV === "production" && !isStrict) {
    return url;
  }

  const contextMsg = context ? ` in ${context}` : "";
  
  // Check for hardcoded localhost
  if (INVALID_PATTERNS.HARDCODED_LOCALHOST.test(url)) {
    const error = `[API Helper] Hardcoded localhost URL detected${contextMsg}: "${url}"\n` +
      `Use proxy route instead: /api/backend/...`;
    console.error(error);
    if (isStrict) {
      throw new Error(error);
    }
  }

  // Check for hardcoded IP address
  if (INVALID_PATTERNS.HARDCODED_IP.test(url)) {
    const error = `[API Helper] Hardcoded IP address detected${contextMsg}: "${url}"\n` +
      `Use proxy route instead: /api/backend/...`;
    console.error(error);
    if (isStrict) {
      throw new Error(error);
    }
  }

  // Check for double /api prefix
  if (INVALID_PATTERNS.DOUBLE_API_PREFIX.test(url)) {
    const error = `[API Helper] Double /api prefix detected${contextMsg}: "${url}"\n` +
      `Should be: /api/backend/... (not /api/backend/api/...)`;
    console.error(error);
    if (isStrict) {
      throw new Error(error);
    }
  }

  // Check for direct HTTP URLs (warning only by default, can be made strict)
  if (INVALID_PATTERNS.DIRECT_HTTP_URL.test(url)) {
    const warning = `[API Helper] Direct HTTP URL detected${contextMsg}: "${url}"\n` +
      `Consider using proxy route for CORS handling: /api/backend/...`;
    console.warn(warning);
    // Note: This is typically a warning, but can throw if strict mode is enabled
    // and this is a concern for your application
  }

  return url;
}

/**
 * Get the correct proxy URL for a backend endpoint.
 * 
 * @param path - The backend path (e.g., "/bots/page" or "bots/page")
 * @param backend - Backend type: "main" (default) or "dt" for intraday backend
 * @returns Proxy URL (e.g., "/api/backend/bots/page")
 */
export function getProxyUrl(path: string, backend: "main" | "dt" = "main"): string {
  // Ensure path starts with /
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  
  // Get base proxy route
  const proxyBase = backend === "dt" ? "/api/dt" : "/api/backend";
  
  // Construct full proxy URL
  const proxyUrl = `${proxyBase}${normalizedPath}`;
  
  // Validate in development
  return validateApiUrl(proxyUrl, "getProxyUrl");
}

/**
 * Check if a URL is a valid proxy route.
 * 
 * @param url - The URL to check
 * @returns true if URL uses proxy route, false otherwise
 */
export function isProxyRoute(url: string): boolean {
  return url.startsWith("/api/backend/") || url.startsWith("/api/dt/");
}

/**
 * Get backend base URL for server-side requests.
 * 
 * ⚠️ IMPORTANT: This should ONLY be used in:
 * - Next.js API routes (server-side)
 * - Server Components (server-side)
 * 
 * Client components should ALWAYS use proxy routes (/api/backend or /api/dt).
 * 
 * The fallback URLs are for local development only. In production, always
 * set BACKEND_URL and DT_BACKEND_URL environment variables.
 * 
 * @param backend - Backend type: "main" (default) or "dt" for intraday backend
 * @returns Backend base URL
 */
export function getBackendUrl(backend: "main" | "dt" = "main"): string {
  if (typeof window !== "undefined") {
    console.warn(
      "[API Helper] getBackendUrl() called in browser. " +
      "Client components should use proxy routes (/api/backend or /api/dt)."
    );
  }

  if (backend === "dt") {
    return process.env.DT_BACKEND_URL || 
           process.env.NEXT_PUBLIC_DT_BACKEND_URL || 
           "https://localhost:8010"; // Fallback for local development
  }

  return process.env.BACKEND_URL || 
         process.env.NEXT_PUBLIC_BACKEND_URL || 
         "https://localhost:8000"; // Fallback for local development
}

/**
 * Wrap fetch with automatic URL validation in development.
 * 
 * @param url - The URL to fetch
 * @param init - Fetch options
 * @param context - Optional context for error messages
 * @param strict - Optional strictness for validation (default: true in dev)
 * @returns Fetch promise
 */
export async function safeFetch(
  url: string,
  init?: RequestInit,
  context?: string,
  strict?: boolean
): Promise<Response> {
  const validatedUrl = validateApiUrl(url, context, strict);
  return fetch(validatedUrl, init);
}

/**
 * Export patterns for external validation if needed
 */
export const API_PATTERNS = INVALID_PATTERNS;

/**
 * Type guard to check if error is an API validation error
 */
export function isApiValidationError(error: unknown): error is Error {
  return error instanceof Error && error.message.includes("[API Helper]");
}
