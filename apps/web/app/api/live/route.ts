import { AccessToken, RoomServiceClient } from "livekit-server-sdk";
import { NextRequest, NextResponse } from "next/server";

// Creates (or reuses) a room tagged with {"mode": "baseline"|"backchannel"}
// via room metadata, then mints a participant token for it. worker.py's
// `_read_mode()` reads that same metadata to pick the pipeline. That's
// the only signal that distinguishes the two configs in the live demo.
export async function POST(req: NextRequest) {
  const { mode } = (await req.json()) as { mode: "baseline" | "backchannel" };
  if (mode !== "baseline" && mode !== "backchannel") {
    return NextResponse.json({ error: "mode must be 'baseline' or 'backchannel'" }, { status: 400 });
  }

  const url = process.env.LIVEKIT_URL;
  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;
  if (!url || !apiKey || !apiSecret) {
    return NextResponse.json({ error: "LIVEKIT_URL/API_KEY/API_SECRET not configured" }, { status: 500 });
  }

  const roomName = `live-${mode}-${Date.now().toString(36)}`;
  const identity = `web-${Math.random().toString(36).slice(2, 8)}`;

  const httpUrl = url.replace(/^wss:/, "https:").replace(/^ws:/, "http:");
  const roomService = new RoomServiceClient(httpUrl, apiKey, apiSecret);
  await roomService.createRoom({ name: roomName, metadata: JSON.stringify({ mode }) });

  const at = new AccessToken(apiKey, apiSecret, { identity });
  at.addGrant({ roomJoin: true, room: roomName, canPublish: true, canSubscribe: true });
  const token = await at.toJwt();

  return NextResponse.json({ url, token, roomName, mode });
}
