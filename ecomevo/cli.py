from __future__ import annotations
import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(prog="ecomevo")
    # The server CLI is intentionally externally bindable; production access is controlled upstream.
    parser.add_argument("--host", default="0.0.0.0")  # nosec
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("ecomevo.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
