# NUSAWIS-be

Backend API NUSAWIS berbasis FastAPI.

## Struktur

```
app/
├── core/       # config, database, security
├── api/        # routing & dependency injection
│   └── v1/
│       └── endpoints/
├── models/     # SQLAlchemy ORM models
├── schemas/    # Pydantic schemas
├── crud/       # operasi database
├── services/   # business logic
└── main.py     # entrypoint FastAPI
alembic/        # database migrations
tests/          # unit & integration tests
```

## Menjalankan

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Dokumentasi API tersedia di `http://127.0.0.1:8000/docs`.

## Migrasi database

```bash
alembic revision --autogenerate -m "pesan migrasi"
alembic upgrade head
```
