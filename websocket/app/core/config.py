from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # url 변경 시 환경변수 DJANGO_BASE_URL로 설정 가능
    DJANGO_BASE_URL: str = "http://127.0.0.1:8000"
    
    class Config:
        env_file = ".env"


settings = Settings()
