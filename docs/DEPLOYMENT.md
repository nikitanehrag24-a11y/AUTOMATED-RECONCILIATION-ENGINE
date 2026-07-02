# Deployment Manual: Automated Reconciliation Engine

## 1. Environment Checklist
Copy `.env.example` to `.env` in the root folder and configure the following variables:

```bash
# Environment Mode
ENVIRONMENT=production
PORT=8000

# Database Connection (Docker environment)
DATABASE_URL=postgresql://postgres:postgres@db:5432/recon_db

# Cache Caching (Docker environment)
REDIS_URL=redis://cache:6379/0

# Secrets Configuration
API_KEY=configure-a-secure-key-here
JWT_SECRET=configure-a-secure-secret-here
```

---

## 2. Docker Compose Deployment
The system is fully containerised and uses three services:
1. `db`: PostgreSQL database with persistent volume `postgres_data`.
2. `cache`: Redis cache for dashboard performance.
3. `app`: Main app service building the `Dockerfile` and initiating both FastAPI (port `8000`) and Streamlit (port `8501`).

To start the system:
```bash
# Build and start services in the background
docker compose up --build -d
```

To view logs:
```bash
docker compose logs -f app
```

To stop services:
```bash
docker compose down -v
```

---

## 3. Local Testing Execution
To run tests inside an isolated compose environment matching the CI pipeline:

```bash
# Build and run the pytest runner service
docker compose -f docker-compose.test.yml up --build --exit-code-from test_runner
```

---

## 4. Troubleshooting & Health Checks
- The application exposes a health check endpoint at `http://localhost:8000/api/v1/dashboard/summary`.
- The container healthcheck uses `curl` to query this endpoint. If it returns non-200 or times out, the container will transition to `unhealthy`.
- In case database connection fails on startup, verify the PostgreSQL healthcheck status: `docker compose ps db` or check logs: `docker compose logs db`.
