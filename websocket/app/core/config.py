from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Django REST API
    DJANGO_BASE_URL: str

    # JWT
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"

    # FastAPI WebSocket
    WS_PORT: int = 5000

    class Config:
        env_file = ".env"


settings = Settings()
