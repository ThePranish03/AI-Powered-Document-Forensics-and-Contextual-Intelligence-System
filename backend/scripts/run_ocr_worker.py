"""Run one persisted OCR job, or poll continuously with --watch."""
import argparse
import asyncio
import threading

from app.services.ocr_worker import process_one, warm_up


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="Keep polling for queued documents")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    print("OCR worker started; warming up the PaddleOCR pipeline", flush=True)

    def warm() -> None:
        try:
            warm_up()
            print("PaddleOCR pipeline is ready", flush=True)
        except Exception as exc:
            print(f"PaddleOCR warm-up failed: {str(exc)[:300]}", flush=True)

    threading.Thread(target=warm, daemon=True).start()
    while True:
        found = await process_one()
        if not args.watch:
            print("Processed one queued document" if found else "No queued documents")
            return
        if not found:
            await asyncio.sleep(max(args.poll_seconds, 0.25))


if __name__ == "__main__":
    asyncio.run(main())
