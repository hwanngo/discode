"""List failed outbox metadata; reset one reviewed row with --retry ID.

Never prints payloads/secrets. Retrying input is safe only with the durable turn
ledger; uncertain execution must be resolved by the operator, never replayed.
"""

import argparse
import asyncio

from sqlalchemy import text

from discode.runtime import AppRuntime


async def run(retry: int | None) -> None:
    runtime = AppRuntime.from_env()
    try:
        async with runtime.db_factory() as db, db.begin():
            if retry is not None:
                result = await db.execute(
                    text("""
                    UPDATE redis_outbox SET attempts=0,error=NULL,next_attempt_at=now()
                    WHERE id=:id AND published_at IS NULL AND error='max_attempts'
                    RETURNING id
                """),
                    {"id": retry},
                )
                print("Reset row:", result.scalar_one_or_none())
            else:
                rows = await db.execute(
                    text("""
                    SELECT id,producer,envelope_type,attempts,created_at FROM redis_outbox
                    WHERE published_at IS NULL AND error='max_attempts' ORDER BY id LIMIT 100
                """)
                )
                for row in rows:
                    print(*row)
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry", type=int)
    asyncio.run(run(parser.parse_args().retry))
