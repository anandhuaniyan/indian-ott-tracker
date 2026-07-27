from fastapi import FastAPI

from app.api.movies import router as movie_router

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


app.include_router(movie_router)