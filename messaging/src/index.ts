import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { timingSafeEqual } from "node:crypto";

import { markdown, Spectrum } from "spectrum-ts";
import { imessage } from "@spectrum-ts/imessage";

const projectId = required("PROJECT_ID");
const projectSecret = required("PROJECT_SECRET");
const bagelApiUrl = process.env.BAGEL_API_URL ?? "http://127.0.0.1:8000";
const bridgeHost = process.env.SPECTRUM_BRIDGE_HOST ?? "127.0.0.1";
const bridgePort = Number(process.env.SPECTRUM_BRIDGE_PORT ?? "8787");
const bridgeToken = process.env.SPECTRUM_BRIDGE_TOKEN ?? "";

const app = await Spectrum({
  projectId,
  projectSecret,
  providers: [imessage.config()],
});
const messages = imessage(app);

const server = createServer(async (request, response) => {
  try {
    if (request.method === "GET" && request.url === "/health") {
      return json(response, 200, { status: "ok" });
    }
    if (request.method !== "POST" || !request.url) {
      return json(response, 404, { error: "Not found" });
    }
    if (!isAuthorized(request)) {
      return json(response, 401, { error: "Invalid bridge token" });
    }

    const body = await readJson(request);
    const to = stringField(body, "to");
    const user = await messages.user(to);
    const space = await messages.space.create(user);

    if (request.url === "/messages") {
      const text = stringField(body, "text");
      const sent = await space.send(body.format === "markdown" ? markdown(text) : text);
      return json(response, 200, { id: sent?.id ?? crypto.randomUUID(), status: "sent" });
    }
    if (request.url === "/typing") {
      if (body.enabled === true) await space.startTyping();
      else await space.stopTyping();
      return json(response, 200, { status: "ok" });
    }
    return json(response, 404, { error: "Not found" });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bridge request failed";
    console.error("bridge request failed", error);
    return json(response, 502, { error: message });
  }
});

server.listen(bridgePort, bridgeHost, () => {
  console.log(`Spectrum bridge listening on http://${bridgeHost}:${bridgePort}`);
});

for await (const [, message] of app.messages) {
  if (message.content.type !== "text" || !message.sender?.id) continue;
  try {
    const response = await fetch(`${bagelApiUrl}/internal/spectrum/inbound`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(bridgeToken ? { Authorization: `Bearer ${bridgeToken}` } : {}),
      },
      body: JSON.stringify({
        delivery_id: `spectrum:${message.platform}:${message.id}`,
        provider_message_id: message.id,
        sender: message.sender.id,
        text: message.content.text,
        timestamp: message.timestamp.toISOString(),
      }),
    });
    if (!response.ok) {
      console.error(`Bagel API rejected inbound message with ${response.status}`);
    }
  } catch (error) {
    console.error("Could not forward inbound message to Bagel API", error);
  }
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function isAuthorized(request: IncomingMessage): boolean {
  const remote = request.socket.remoteAddress ?? "";
  const local = remote === "127.0.0.1" || remote === "::1" || remote === "::ffff:127.0.0.1";
  if (!bridgeToken) return local;
  const supplied = request.headers.authorization?.replace(/^Bearer /, "") ?? "";
  const expectedBuffer = Buffer.from(bridgeToken);
  const suppliedBuffer = Buffer.from(supplied);
  return expectedBuffer.length === suppliedBuffer.length && timingSafeEqual(expectedBuffer, suppliedBuffer);
}

async function readJson(request: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > 32_000) throw new Error("Request body is too large");
    chunks.push(buffer);
  }
  const value: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid JSON body");
  return value as Record<string, unknown>;
}

function stringField(body: Record<string, unknown>, name: string): string {
  const value = body[name];
  if (typeof value !== "string" || !value.trim()) throw new Error(`${name} is required`);
  return value.trim();
}

function json(response: ServerResponse, status: number, body: object): void {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}
