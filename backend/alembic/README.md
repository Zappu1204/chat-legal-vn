# Database migrations

Alembic migrations for the backend application schema (users, conversations,
messages, system_config).

```bash
# from backend/, with the venv active and CLVN_DATABASE_URL set
alembic revision --autogenerate -m "init schema"
alembic upgrade head
```

The dev/test path uses `SQLModel.metadata.create_all` in the app lifespan, so
migrations are only required for production deployments where you want
versioned, reversible schema changes.
