from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from core.config import settings

if not settings.DATABASE_URL or settings.DATABASE_URL.startswith("sqlite"):
    raise RuntimeError(
        "DATABASE_URL must be a Supabase/Postgres URL "
        "(postgresql+psycopg://...). SQLite is no longer supported — "
        "set DATABASE_URL in backend/.env."
    )

# Supabase/Postgres: pooled connections that recover from the pooler dropping
# idle ones.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
