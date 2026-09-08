import { approvedBrief } from "@/server/analysis-results";
import { beginOperation } from "@/server/telemetry";
import { admitPublic } from "@/server/public-access"
import { createAgentUIStreamResponse } from "ai";
import { createDiscoveryAgent } from "@/server/discovery-agent";
import type { QueryHelpers } from "@/types/query-helpers";
import { assistantMessages } from "@/server/assistant-input";

export const runtime = "nodejs";
export const maxDuration = 190;
let active = 0;

export async function POST(request: Request) {
  const finish = beginOperation("assistant");
  if (
    !process.env.OPENAI_API_KEY ||
    !process.env.PERIPLUS_AI_MODEL ||
    !process.env.PERIPLUS_QUERY_API_TOKEN
  ) {
    finish("unconfigured");
    return Response.json(
      { detail: "The assistant is not configured yet. Try the SQL console." },
      { status: 503 },
    );
  }
  if (active >= 2) {
    finish("rejected");
    return Response.json(
      { detail: "The assistant is busy. Try again shortly." },
      { status: 429, headers: { "Retry-After": "5" } },
    );
  }
  let messages;
  let approved;
  try {
    const reader = request.body?.getReader();
    if (!reader) throw new Error("Missing body");
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 64_000) {
        finish("invalid");
        await reader.cancel();
        return Response.json(
          { detail: "Conversation is too long. Start a new dataset." },
          { status: 413 },
        );
      }
      chunks.push(value);
    }
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    messages = assistantMessages(body);
    approved = approvedBrief(body.approval, process.env.PERIPLUS_QUERY_API_TOKEN!);
  } catch {
    finish("invalid");
    return Response.json(
      { detail: "Please describe a dataset or start a new definition." },
      { status: 400 },
    );
  }
  // Admission is per process; production ingress owns aggregate rate limits.
  if (active >= 2) {
    finish("rejected");
    return Response.json({ detail: "The assistant is busy." }, { status: 429 });
  }
  const denial = await admitPublic("assistant", request.signal)
  if (denial) { finish(denial.status >= 500 ? "failed" : "rejected"); return denial }
  // Recheck after the shared admission await.
  if (active >= 2) { finish("rejected"); return Response.json({ detail: "The assistant is busy." }, { status: 429, headers: { "Retry-After": "5" } }) }
  active++;
  let released = false;
  const release = () => {
    if (!released) {
      released = true;
      active--;
    }
  };
  const signal = AbortSignal.any([
    request.signal,
    AbortSignal.timeout(180_000),
  ]);
  const aborted = () => {
    finish(request.signal.aborted ? "cancelled" : "timeout");
    release();
  };
  signal.addEventListener("abort", aborted, { once: true });
  try {
    const helperResponse = await fetch(
      new URL(
        "/query/helpers",
        process.env.PERIPLUS_QUERY_URL ?? "http://127.0.0.1:8010",
      ),
      {
        headers: {
          authorization: `Bearer ${process.env.PERIPLUS_QUERY_API_TOKEN}`,
        },
        cache: "no-store",
        signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
      },
    );
    if (!helperResponse.ok) throw new Error("SQL helper catalogue unavailable");
    const helpers: QueryHelpers = await helperResponse.json();
    return await createAgentUIStreamResponse({
      agent: createDiscoveryAgent(helpers, approved),
      uiMessages: messages,
      abortSignal: signal,
      timeout: 180_000,
      sendReasoning: false,
      onEnd: () => {
        signal.removeEventListener("abort", aborted);
        finish("success");
        release();
      },
      onError: () => {
        finish("failed");
        release();
        return "The assistant could not finish. Retry with your dataset definition or use the SQL console.";
      },
    });
  } catch {
    finish(
      signal.aborted
        ? request.signal.aborted
          ? "cancelled"
          : "timeout"
        : "failed",
    );
    release();
    return Response.json(
      { detail: "The assistant is temporarily unavailable." },
      { status: 502 },
    );
  }
}
