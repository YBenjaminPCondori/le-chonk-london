export interface Env {
  BACKEND_URL: string;
}

/** Proxy all HTTP + WebSocket traffic to the ChonkVid origin (Tunnel or local uvicorn). */
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const base = (env.BACKEND_URL || "").trim().replace(/\/$/, "");
    if (!base) {
      return new Response(
        "ChonkVid Worker: set BACKEND_URL (e.g. your Cloudflare Tunnel URL).\n",
        { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }
    const incoming = new URL(request.url);
    const target = new URL(incoming.pathname + incoming.search, base);
  const headers = new Headers(request.headers);
    headers.delete("host");
    const proxied = new Request(target.toString(), {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual",
      // @ts-expect-error duplex for streaming bodies
      duplex: "half",
    });
    return fetch(proxied);
  },
};