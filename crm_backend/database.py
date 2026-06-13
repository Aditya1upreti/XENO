# crm_backend/database.py

from sqlmodel import SQLModel, create_engine, Session

sqlite_file_name = "nexus_crm.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

# Added timeout=30 to connect_args to avoid SQLite database locked exceptions under rapid concurrent webhooks
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False, "timeout": 30})

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session