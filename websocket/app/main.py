from fastapi import FastAPI

from app.ws.endpoints import router as ws_router

app = FastAPI(title="KLAV WebSocket Server")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


app.include_router(ws_router)
