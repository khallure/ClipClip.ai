/** Streaming proxy for the isolated Installer DEV backend. */
import { isPackagedDesktop, validDesktopRequest } from "@/lib/desktop-auth";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const backend = "http://127.0.0.1:8300";
const frontend = "http://127.0.0.1:3300";
const requestHeaders = ["content-type", "content-length", "range", "if-range", "if-none-match", "if-modified-since", "origin", "accept"];
const hopHeaders = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "x-podcut-run-token", "x-podcut-ui-token"]);

function runIdentity() {
  // The launcher and backend validate these filesystem roots before startup.
  // Keeping absolute data-directory literals out of the server bundle also
  // prevents NFT from mistaking the user's profile directory for an asset.
  const projectRoot = process.env.PODCUT_PROJECT_ROOT || "";
  const profileRoot = process.env.PODCUT_PROFILE_ROOT || "";
  if (process.env.PODCUT_API_ORIGIN !== backend || process.env.PODCUT_PROFILE_ID !== "installer-dev" ||
      !projectRoot || !profileRoot ||
      !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(process.env.PODCUT_RUN_ID || "") ||
      !/^[a-f0-9]{64}$/i.test(process.env.PODCUT_RUN_TOKEN || "")) {
    throw new Error("Installer DEV runtime identity is missing or invalid.");
  }
  return { runId: process.env.PODCUT_RUN_ID!, token: process.env.PODCUT_RUN_TOKEN!, projectRoot, profileRoot };
}

async function verifiedBackend(run: { runId: string; token: string; projectRoot: string; profileRoot: string }) {
  const response = await fetch(`${backend}/api/runtime-identity`, {
    headers: { "x-podcut-run-token": run.token },
    cache: "no-store", redirect: "error", signal: AbortSignal.timeout(2000),
  });
  if (!response.ok) throw new Error("Installer DEV backend identity is unavailable.");
  const identity = await response.json();
  if (identity.profile_id !== "installer-dev" || identity.project_root !== run.projectRoot || identity.profile_root !== run.profileRoot ||
      identity.run_id !== run.runId || identity.port !== 8300 || identity.frontend_port !== 3300 ||
      identity.backend_host !== "127.0.0.1" || identity.protocol !== "podcut-installer-dev-v1" ||
      (isPackagedDesktop() ? identity.build !== "phase3a" || identity.packaged !== true ||
        identity.packaged_root !== process.env.PODCUT_PACKAGED_ROOT : identity.build !== "phase2a")) {
    throw new Error("Installer DEV backend identity does not match.");
  }
}

async function forward(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const url = new URL(request.url);
  const origin = request.headers.get("origin");
  // NextURL canonicalizes loopback 127.* to localhost internally. The incoming
  // Host and browser Origin still must match the exact DEV address below.
  const localRequestUrl = url.origin === frontend || url.origin === "http://localhost:3300";
  if (!localRequestUrl || request.headers.get("host") !== "127.0.0.1:3300" || (origin !== null && origin !== frontend) ||
      !validDesktopRequest(request)) {
    return Response.json({ detail: "Use the Installer DEV address on port 3300." }, { status: 403 });
  }
  const { path } = await context.params;
  if (!path.length || path.some(part => !/^[a-zA-Z0-9_.-]+$/.test(part) || part === "." || part === "..")) {
    return Response.json({ detail: "Invalid local API path." }, { status: 400 });
  }
  try {
    const run = runIdentity();
    await verifiedBackend(run);
    const headers = new Headers();
    for (const name of requestHeaders) {
      const value = request.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    headers.set("accept-encoding", "identity");
    // The browser cannot supply or read this server-only launch credential.
    headers.set("x-podcut-run-token", run.token);
    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const upstream = await fetch(`${backend}/api/${path.join("/")}${url.search}`, {
      method: request.method, headers,
      body: hasBody ? request.body : undefined,
      duplex: "half", signal: request.signal, cache: "no-store", redirect: "manual",
    } as RequestInit & { duplex: "half" });
    const responseHeaders = new Headers();
    upstream.headers.forEach((value, name) => { if (!hopHeaders.has(name.toLowerCase())) responseHeaders.set(name, value); });
    responseHeaders.set("cache-control", "no-store");
    responseHeaders.set("x-podcut-proxy-profile", "installer-dev");
    responseHeaders.set("x-podcut-proxy-run-id", run.runId);
    return new Response(request.method === "HEAD" ? null : upstream.body, {
      status: upstream.status, statusText: upstream.statusText, headers: responseHeaders,
    });
  } catch {
    return Response.json({ detail: "The Installer DEV processing service could not be verified. Restart the DEV launcher and retry." }, { status: 502 });
  }
}
export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE, forward as HEAD, forward as OPTIONS };
