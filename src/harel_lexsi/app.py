from fastapi import FastAPI

app = FastAPI(title="Harel Lexsi")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
