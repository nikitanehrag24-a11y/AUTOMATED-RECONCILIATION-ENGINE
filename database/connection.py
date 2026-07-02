from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from config.settings import settings
from database.models import Base

# Setup engine with connection pool parameters
# For PostgreSQL: pool_size, max_overflow, pool_recycle are useful.
# For SQLite, these parameters are not supported or behave differently, so we check the URL schema.
is_sqlite = settings.DATABASE_URL.startswith("sqlite")

if is_sqlite:
    # SQLite parameters
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
else:
    # PostgreSQL parameters for production scaling
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_pre_ping=True
    )

# Create Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db_session = scoped_session(SessionLocal)

def init_db():
    """Initialise database tables."""
    Base.metadata.create_all(bind=engine)

def get_db():
    """Dependency for FastAPI endpoints to get a clean database session context."""
    db = SessionLocal()
    try:
      yield db
    finally:
      db.close()
