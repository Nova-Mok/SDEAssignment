"""Create (or recreate) a LiveKit room with `{"mode": ...}` metadata, so the
dispatched agent's `worker._read_mode()` picks the right pipeline. Used for
manual smoke tests and by the live-demo web page's room-creation route.

    python scripts/create_room.py my-room backchannel
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from livekit import api  # noqa: E402


async def main(room_name: str, mode: str) -> None:
    async with api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    ) as lk:
        room = await lk.room.create_room(
            api.CreateRoomRequest(name=room_name, metadata=json.dumps({"mode": mode}))
        )
        print(f"created room {room.name!r} (sid={room.sid}) metadata={room.metadata!r}")


if __name__ == "__main__":
    room_name = sys.argv[1] if len(sys.argv) > 1 else "backchannel-smoketest"
    mode = sys.argv[2] if len(sys.argv) > 2 else "backchannel"
    asyncio.run(main(room_name, mode))
