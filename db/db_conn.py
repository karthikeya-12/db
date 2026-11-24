from contextlib import contextmanager
from uuid import uuid7

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    create_engine,
    func,
    inspect,
    text,
)
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from src.project import settings

DATABASE_URL = URL.create(
    drivername="postgresql+psycopg2",
    username="postgres",
    password=settings.DB_PASSWORD,
    host=settings.DB_HOST,
    port=int(settings.DB_PORT),
    database=settings.DB,
)


engine = create_engine(
    DATABASE_URL,
    # Connection pool
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
    connect_args={
        "application_name": "fastapi_application",
        "options": f"-csearch_path={settings.DB_SCHEMA}",
    },
    echo=False,
    future=True,
)


SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


Base = automap_base()
Base1 = declarative_base()

Base.prepare(
    autoload_with=engine,
    schema=settings.DB_SCHEMA,
)


def get_db():
    db = SessionLocal()

    try:
        yield db

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@contextmanager
def session_scope():
    """Manage database session and transactions."""

    db = SessionLocal()

    try:
        yield db
        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def check_db_connection() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return True

    except SQLAlchemyError:
        return False


def close_database():
    engine.dispose()


def get_db_details():
    tables = inspect(engine).get_table_names(schema=settings.DB_SCHEMA)
    print(tables)


class User(Base1):
    __tablename__ = "users"
    __table_args__ = {"schema": settings.DB_SCHEMA}

    id = Column(String(30), primary_key=True, index=True)
    name = Column(String(100), index=True, unique=True)
    age = Column(Integer)
    is_active = Column(Boolean)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    orders = relationship("Orders", back_populates="user")


class Orders(Base1):
    __tablename__ = "orders"
    __table_args__ = {"schema": settings.DB_SCHEMA}

    id = Column(String(30), primary_key=True, index=True)
    user_id = Column(String(30), ForeignKey("sample_schema.users.id"), nullable=False)
    order_name = Column(String(100))
    amount = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="orders")


def create_table():
    Base1.metadata.create_all(bind=engine)


def alter_table():

    with engine.begin() as connection:

        # connection.execute(text("""
        #         ALTER TABLE sample_schema.users
        #         ALTER COLUMN id TYPE VARCHAR(36)
        #     """))

        connection.execute(text("""
            ALTER TABLE sample_schema.orders
            ALTER COLUMN id TYPE VARCHAR(36),
            ALTER COLUMN user_id TYPE VARCHAR(36)
        """))

    print("Tables altered successfully!")


def insert_values():
    table = Base.classes.users
    # values = [table(id=str(uuid7()), name="Navya", age=24, is_active=True)]
    # with session_scope() as session:
    #     res = session.add_all(values)
    #     session.flush()
    # print([i.id for i in res])
    with session_scope() as session:
        # res = session.query(table).where(table.name == "Navya").all()
        # print([i.__dict__ for i in res])
        # res = session.query(table).filter(table.name == "Navya Sri").one()
        # print([i.__dict__ for i in res])
        # res.name = "Navya Sri"
        # session.flush()
        value = table(id=str(uuid7()), name="Karthik", age=24, is_active=True)
        session.add(value)
        session.flush()
        table2 = Base.classes.orders
        order = table2(
            id=str(uuid7()), user_id=value.id, order_name="Laptop", amount=20000
        )
        session.add(order)
        session.flush()
        res = session.query(table2).filter(table2.user_id == table.id).all()
        print([i.__dict__ for i in res])


if __name__ == "__main__":
    # get_db_details()
    insert_values()
