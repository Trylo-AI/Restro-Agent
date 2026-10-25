# One image, two Cloud Run services (docs/architecture.md#infrastructure). Build from the repo root:
#   docker build -f backend/Dockerfile -t restro-backend .
# RESTRO_SERVICE=api (public) or worker (internal) picks the app; Cloud Run sets PORT.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The app reads contracts/llm_output.schema.json and contracts/allergens.json next to backend/.
WORKDIR /srv/backend
COPY backend/requirements.lock ./
RUN pip install -r requirements.lock
COPY contracts/llm_output.schema.json contracts/allergens.json /srv/contracts/
# Run from source (not an installed copy): app/ finds ../contracts relative to its own files.
COPY backend/app ./app
RUN useradd --system --uid 10001 restro

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
USER restro
CMD ["sh", "-c", "exec uvicorn app.main_${RESTRO_SERVICE:-api}:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
