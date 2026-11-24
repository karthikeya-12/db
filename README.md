# PostgreSQL Database Connection & SQLAlchemy ORM

A Python-based PostgreSQL database integration project using **SQLAlchemy ORM** and **Psycopg2**. This project demonstrates database connectivity, connection pooling, session management, automatic schema reflection, declarative models, table creation, and CRUD operations.

## 🚀 Features

- **PostgreSQL Connectivity:** Connect to PostgreSQL using SQLAlchemy and Psycopg2.
- **Connection Pooling:** Efficient database connection management with configurable pool settings.
- **Session Management:** Centralized database session handling with automatic commit, rollback, and cleanup.
- **Database Reflection (Automap):** Automatically reflect existing database tables and map them to Python classes.
- **Declarative ORM Models:** Define database tables using SQLAlchemy declarative models.
- **Relationship Management:** Establish relationships between tables using foreign keys and ORM relationships.
- **Table Creation:** Create database tables dynamically using SQLAlchemy metadata.
- **Table Alteration:** Execute raw SQL statements for schema modifications.
- **Database Health Check:** Verify database connectivity.
- **UUID Generation:** Generate unique identifiers for database records.

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend programming |
| PostgreSQL | Relational database |
| SQLAlchemy | ORM and database management |
| Psycopg2 | PostgreSQL database driver |
| UUID | Unique record identification |
| FastAPI | Compatible with API-based database integration |

## 📁 Project Structure

```text
project/
│
├── db/
│   ├── __init__.py
│   └── db_conn.py
│
├── src/
│   └── project/
│       └── settings.py
│
├── .env
├── .gitignore
├── .python-version
├── main.py
├── pyproject.toml
├── uv.lock
└── README.md
```

## ⚙️ Prerequisites

Make sure the following are installed:

- Python
- PostgreSQL
- UV package manager
- PostgreSQL Psycopg2 driver

## 📦 Installation

### 1. Clone the repository

```bash
git clone <repository-url>
cd project
```

### 2. Create a virtual environment

Using Python:

```bash
python -m venv .venv
```

Activate the environment on Windows:

```bash
.venv\Scripts\activate
```

### 3. Install dependencies

Using UV:

```bash
uv sync
```

Alternatively, install the required packages:

```bash
pip install sqlalchemy psycopg2-binary
```

## 🔐 Environment Configuration

Create a `.env` file in the project root directory.

Configure the following environment variables:

```env
DB_HOST=localhost
DB_PORT=5432
DB=your_database_name
DB_SCHEMA=sample_schema
DB_PASSWORD=your_database_password
```

Ensure that your `settings.py` loads these environment variables and exposes them through the `settings` object.

**Note:** Never commit your `.env` file or database credentials to Git. Use `.env.example` to share configuration templates.

## 🔌 Database Connection

The project uses SQLAlchemy's `create_engine()` to establish a PostgreSQL connection.

```python
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
    echo=False,
    future=True
)
```

### Connection Pool Configuration

| Parameter | Value | Description |
|---|---:|---|
| pool_size | 10 | Maintains 10 persistent connections |
| max_overflow | 20 | Allows up to 20 additional temporary connections |
| pool_timeout | 30 | Waits up to 30 seconds for a connection |
| pool_recycle | 1800 | Recycles connections after 30 minutes |
| pool_pre_ping | True | Checks connection health before reuse |
| echo | False | Disables SQL query logging |

The application also configures the PostgreSQL connection's `search_path` using the configured database schema.

## 🗄️ Database Session Management

The project provides two approaches for managing database sessions.

### 1. get_db()

Designed for dependency injection in FastAPI endpoints.

```python
def get_db():
    db = SessionLocal()

    try:
        yield db

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
```

Example FastAPI integration:

```python
from fastapi import Depends
from sqlalchemy.orm import Session

@app.get("/users")
def get_users(db: Session = Depends(get_db)):
    return db.query(User).all()
```

### 2. session_scope()

A context manager that handles transaction management automatically.

```python
with session_scope() as session:
    session.add(user)
```

- Commits the transaction when execution succeeds.
- Rolls back the transaction when an exception occurs.
- Closes the session after execution.

## 🔄 Database Reflection (Automap)

SQLAlchemy Automap is used to automatically reflect existing database tables into Python ORM classes.

```python
Base = automap_base()

Base.prepare(
    autoload_with=engine,
    schema=settings.DB_SCHEMA
)
```

This approach is useful when working with existing databases where tables and their structures are already defined.

Access reflected tables dynamically:

```python
users_table = Base.classes.users
orders_table = Base.classes.orders
```

This avoids manually defining ORM classes for every existing database table.

## 🏗️ Declarative ORM Models

The project also supports manually defining database models using SQLAlchemy's declarative approach.

### User Model

```python
class User(Base1):
    __tablename__ = "users"
    __table_args__ = {"schema": settings.DB_SCHEMA}

    id = Column(String(30), primary_key=True)
    name = Column(String(100), unique=True)
    age = Column(Integer)
    is_active = Column(Boolean)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    orders = relationship("Orders", back_populates="user")
```

### Orders Model

```python
class Orders(Base1):
    __tablename__ = "orders"
    __table_args__ = {"schema": settings.DB_SCHEMA}

    id = Column(String(30), primary_key=True)

    user_id = Column(
        String(30),
        ForeignKey("sample_schema.users.id"),
        nullable=False
    )

    order_name = Column(String(100))
    amount = Column(Integer)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    user = relationship("User", back_populates="orders")
```

The relationship between `User` and `Orders` establishes a one-to-many association:

- One user can have multiple orders.
- Each order belongs to one user.

## 🛠️ Database Operations

### Create Tables

```python
def create_table():
    Base1.metadata.create_all(bind=engine)
```

Creates the tables defined using the declarative models if they do not already exist.

### Alter Tables

The project supports executing raw SQL statements through SQLAlchemy.

```python
with engine.begin() as connection:
    connection.execute(text("""
        ALTER TABLE sample_schema.orders
        ALTER COLUMN id TYPE VARCHAR(36),
        ALTER COLUMN user_id TYPE VARCHAR(36)
    """))
```

### Insert Records

The `insert_values()` function demonstrates inserting user and order records.

```python
with session_scope() as session:

    user = User(
        id=str(uuid7()),
        name="Karthik",
        age=24,
        is_active=True
    )

    session.add(user)
    session.flush()

    order = Orders(
        id=str(uuid7()),
        user_id=user.id,
        order_name="Laptop",
        amount=20000
    )

    session.add(order)
    session.flush()
```

### Query Records

SQLAlchemy ORM supports querying records using filters.

```python
with session_scope() as session:

    results = (
        session.query(Orders)
        .filter(Orders.user_id == user.id)
        .all()
    )
```

## 🩺 Database Health Check

Use the connection health-check function to verify database connectivity.

```python
if check_db_connection():
    print("Database connected successfully!")
else:
    print("Database connection failed!")
```

## 🔒 Database Resource Cleanup

Dispose of the connection pool when shutting down the application.

```python
def close_database():
    engine.dispose()
```

For FastAPI applications, this can be integrated into the application lifespan shutdown logic.

## ▶️ Running the Project

Run the application entry point:

```bash
python main.py
```

Or, if using UV:

```bash
uv run python main.py
```

## 📌 Important Notes

1. Ensure PostgreSQL is running before starting the application.
2. The configured database and schema must exist.
3. Automap reflection requires the existing database tables to be accessible.
4. Declarative models and Automap use separate metadata objects (`Base1` and `Base`).
5. `create_table()` creates tables registered with `Base1`; it does not create tables from the reflected Automap metadata.
6. Ensure foreign key schema references match the configured database schema.
7. Keep database credentials in environment variables.

## 📚 References

- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/en/20/orm/)
- [SQLAlchemy Automap](https://docs.sqlalchemy.org/en/20/orm/extensions/automap.html)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Psycopg2 Documentation](https://www.psycopg.org/docs/)

---

**Author:** Project Development Team

**Project:** PostgreSQL Database Integration using SQLAlchemy