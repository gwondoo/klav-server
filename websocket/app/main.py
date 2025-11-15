from fastapi import FastAPI
import os
from dotenv import load_dotenv

from app.ws.endpoints import router as ws_router

load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET", "dev-jwt-secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

app = FastAPI(title="KLAV WebSocket Server")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


app.include_router(ws_router)
