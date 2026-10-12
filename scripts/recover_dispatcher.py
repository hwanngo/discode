"""List retained Discord dead-letter metadata, or explicitly replay a reviewed ID.

Payloads are never printed. A replay can duplicate a Discord message after its
nonce deduplication window, so inspect delivery state before using --retry.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from discode.dispatcher.consumer import DISPATCHER_DEADLETTERS, DISPATCHER_STREAM
from discode.runtime import AppRuntime


async def run(retry: str | None) -> None:
    runtime = AppRuntime.from_env()
    try:
        if retry is not None:
            # Atomic move: concurrent recovery invocations cannot replay the
            # same retained entry twice. Only trusted operator-supplied IDs.
            result = await runtime.redis.eval(
                """
                local raw = redis.call('HGET', KEYS[1], ARGV[1])
                if not raw then return false end
                local fields = cjson.decode(raw)
                local args = {}
                for k,v in pairs(fields) do
                    table.insert(args,k)
                    table.insert(args,v)
                end
                local id = redis.call('XADD', KEYS[2], '*', unpack(args))
                redis.call('HDEL', KEYS[1], ARGV[1])
                return id
                """,
                2,
                DISPATCHER_DEADLETTERS,
                DISPATCHER_STREAM,
                retry,
            )
            print("Replay stream ID:", result)
        else:
            count = 0
            async for message_id, raw in runtime.redis.hscan_iter(DISPATCHER_DEADLETTERS):
                fields = json.loads(raw)
                print(message_id.decode(), fields.get("envelope_type", "unknown"))
                count += 1
                if count >= 100:
                    print("Showing at most 100 retained entries.")
                    break
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry", help="Retained Redis message ID to replay")
    asyncio.run(run(parser.parse_args().retry))
