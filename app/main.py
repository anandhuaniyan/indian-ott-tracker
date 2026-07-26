from fastapi import FastAPI


app = FastAPI(
    title="Indian OTT Tracker",
    version="0.1.0"
)


@app.get("/")
def root():
    return {
        "project": "Indian OTT Tracker",
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }