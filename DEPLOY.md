# NarraWorld Production Deploy

This project is packaged as a single Docker web service for production:

- Flask/Gunicorn serves `/api/*`.
- Flask serves the built Vite frontend from `frontend/dist`.
- Uploaded files, generated worlds, generation jobs, reports, and simulations are persisted under `/app/backend/uploads`.

## Required environment variables

Set these in your hosting provider dashboard. Do not commit `.env`.

```bash
LLM_API_KEY=your_llm_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4o-mini
ZEP_API_KEY=your_zep_key
FLASK_DEBUG=false
WEB_CONCURRENCY=1
GUNICORN_THREADS=8
GUNICORN_TIMEOUT=300
UPLOAD_FOLDER=/app/backend/uploads
OASIS_SIMULATION_DATA_DIR=/app/backend/uploads/simulations
FRONTEND_DIST_DIR=/app/frontend/dist
SECRET_KEY=replace_with_a_long_random_string
```

## Railway

1. Push this repository to GitHub.
2. Create a Railway project from the GitHub repo.
3. Railway should detect `Dockerfile`; keep the Dockerfile builder.
4. Add a Volume and mount it to:

```bash
/app/backend/uploads
```

5. Add the environment variables above.
6. Deploy and open the generated Railway domain.
7. Check:

```bash
/health
/api/story/list?limit=1
```

## Render

1. Push this repository to GitHub.
2. In Render, create a Blueprint from `render.yaml`, or create a Web Service manually with Docker.
3. Add a persistent disk mounted at:

```bash
/app/backend/uploads
```

4. Add the environment variables above.
5. Deploy and open the generated `onrender.com` domain.
6. Check:

```bash
/health
/api/story/list?limit=1
```

## Local production check

```bash
npm --prefix frontend run build
cd backend
FRONTEND_DIST_DIR=../frontend/dist FLASK_DEBUG=false uv run gunicorn --bind 127.0.0.1:5055 --workers 1 --threads 4 --timeout 120 'app:create_app()'
```

Open:

```bash
http://127.0.0.1:5055
```
