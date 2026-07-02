import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    ENVIRONMENT: str = Field(default="development")
    PORT: int = Field(default=8000)
    
    # DB
    DATABASE_URL: str = Field(default="sqlite:///./recon_test.db")
    
    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    
    # Security
    API_KEY: str = Field(default="dev-api-key-change-in-production")
    JWT_SECRET: str = Field(default="dev-jwt-secret-change-in-production")
    ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60)
    
    # Configuration paths
    BANK_CONFIG_DIR: str = Field(default=os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "bank_configurations"))

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate settings
settings = Settings()
