import argparse
import os

import uvicorn


def run() -> None:
    parser = argparse.ArgumentParser(description="Run the parking API server.")
    parser.add_argument(
        "--host", default=os.environ.get("PARKING_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PARKING_PORT", "5000"))
    )
    args = parser.parse_args()
    uvicorn.run("server.main:app", host=args.host, port=args.port)
