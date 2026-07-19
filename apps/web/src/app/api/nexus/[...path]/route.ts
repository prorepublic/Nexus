import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { NextRequest, NextResponse } from "next/server";

/**
 * Same-origin proxy to the Nexus control plane (ADR-013).
 *
 * The local-owner credential lives in a chmod-600 file on the owner's
 * machine. This route runs server-side in the Next.js process, reads the
 * token, and attaches it on the loopback hop to the API — so the token is
 * never embedded in browser JavaScript, never appears in the client bundle,
 * and never crosses an origin boundary.
 */

const API_BASE =
  process.env.NEXUS_INTERNAL_API_URL ?? "http://127.0.0.1:8400";
const TOKEN_FILE =
  process.env.NEXUS_OWNER_TOKEN_FILE ?? join(homedir(), ".nexus", "owner-token");

// Only control-plane API paths may be proxied.
const ALLOWED_PREFIXES = ["api/", "health"];

function readOwnerToken(): string | null {
  try {
    return readFileSync(TOKEN_FILE, "utf-8").trim() || null;
  } catch {
    return null;
  }
}

async function proxy(request: NextRequest, path: string[]): Promise<NextResponse> {
  const target = path.join("/");
  if (!ALLOWED_PREFIXES.some((prefix) => target === "health" || target.startsWith(prefix))) {
    return NextResponse.json({ detail: "path not proxied" }, { status: 404 });
  }
  const url = `${API_BASE}/${target}${request.nextUrl.search}`;
  const headers: Record<string, string> = {
    "X-Nexus-Client": "dashboard-proxy",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    const token = readOwnerToken();
    if (!token) {
      return NextResponse.json(
        {
          detail:
            "owner token not found on this machine; start the control plane once (nexus start) to generate it",
        },
        { status: 503 },
      );
    }
    headers["X-Nexus-Owner-Token"] = token;
    headers["Content-Type"] = request.headers.get("content-type") ?? "application/json";
  }

  let body: string | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.text();
  }

  try {
    const upstream = await fetch(url, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
    });
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: `control plane unreachable at ${API_BASE}` },
      { status: 502 },
    );
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  return proxy(request, path);
}
