/**
 * Next.js API Route: /api/dt/[...path]
 * 
 * This proxy forwards frontend requests to the DT (intraday) backend server.
 * 
 * Routing Logic:
 * - Forward path as-is (no prefix transformation needed)
 * - DT backend routes are at root level
 * 
 * Example transformations:
 * - /api/dt/health → http://dt-backend:8010/health
 * - /api/dt/jobs/status → http://dt-backend:8010/jobs/status
 * - /api/dt/data/positions → http://dt-backend:8010/data/positions
 */

import { NextRequest, NextResponse } from "next/server";

// Supported HTTP methods
const ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"];

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return handleRequest("GET", request, context);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return handleRequest("POST", request, context);
}

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return handleRequest("PUT", request, context);
}

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return handleRequest("PATCH", request, context);
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return handleRequest("DELETE", request, context);
}

export async function OPTIONS(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return handleRequest("OPTIONS", request, context);
}

async function handleRequest(
  method: string,
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  try {
    // Handle Next.js 15 async params
    const params = await Promise.resolve(context.params);
    const pathParts = params.path || [];

    // Get DT backend URL from environment
    const dtBackendUrl =
      process.env.DT_BACKEND_URL || process.env.NEXT_PUBLIC_DT_BACKEND_URL;

    if (!dtBackendUrl) {
      console.error("[DT Proxy] DT_BACKEND_URL not configured");
      return NextResponse.json(
        { error: "DT backend URL not configured" },
        { status: 502 }
      );
    }

    // Construct target path (forward as-is, no prefix manipulation)
    const targetPath = pathParts.length > 0 ? `/${pathParts.join("/")}` : "/";

    // Preserve query string
    const url = new URL(request.url);
    const queryString = url.search;
    const targetUrl = `${dtBackendUrl}${targetPath}${queryString}`;

    console.log(
      `[DT Proxy] ${method} ${url.pathname} → ${targetUrl}`
    );

    // Prepare request headers
    const headers = new Headers();
    request.headers.forEach((value, key) => {
      // Skip host header as it should be set by fetch
      if (key.toLowerCase() !== "host") {
        headers.set(key, value);
      }
    });

    // Prepare fetch options
    const fetchOptions: RequestInit & { duplex?: string } = {
      method,
      headers,
      // Handle Node 18+ duplex streaming for request bodies
      duplex: "half",
    };

    // Add body for methods that support it
    if (["POST", "PUT", "PATCH"].includes(method)) {
      try {
        const body = await request.arrayBuffer();
        if (body.byteLength > 0) {
          fetchOptions.body = body;
        }
      } catch (e) {
        console.warn("[DT Proxy] Failed to read request body:", e);
      }
    }

    // Forward request to DT backend
    const response = await fetch(targetUrl, fetchOptions);

    // Get response body
    const responseBody = await response.arrayBuffer();

    // Create response with same status and headers
    const proxyResponse = new NextResponse(responseBody, {
      status: response.status,
      statusText: response.statusText,
    });

    // Copy response headers
    response.headers.forEach((value, key) => {
      proxyResponse.headers.set(key, value);
    });

    return proxyResponse;
  } catch (error: unknown) {
    const errorMessage = error instanceof Error ? error.message : "Unknown error";
    console.error("[DT Proxy] Request failed:", error);
    return NextResponse.json(
      {
        error: "DT backend request failed",
        message: errorMessage,
      },
      { status: 502 }
    );
  }
}
