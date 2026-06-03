FROM python:3.12-slim AS version

WORKDIR /src

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY .git ./.git

RUN set -eux; \
    revision="$(git rev-list --count HEAD 2>/dev/null || true)"; \
    commit="$(git rev-parse HEAD 2>/dev/null || true)"; \
    short_commit="$(git rev-parse --short=7 HEAD 2>/dev/null || true)"; \
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"; \
    version="dev"; \
    if [ -n "$revision" ] && [ -n "$short_commit" ]; then version="r${revision}.${short_commit}"; \
    elif [ -n "$short_commit" ]; then version="$short_commit"; fi; \
    printf '{"version":"%s","revision":"%s","commit":"%s","short_commit":"%s","branch":"%s","source":"docker"}\n' "$version" "$revision" "$commit" "$short_commit" "$branch" > /redwave-version.json

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY static ./static
COPY --from=version /redwave-version.json ./redwave-version.json

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
