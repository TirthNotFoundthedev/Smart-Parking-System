import uvicorn


def run() -> None:
    uvicorn.run("server.main:app", host="127.0.0.1", port=5000)
