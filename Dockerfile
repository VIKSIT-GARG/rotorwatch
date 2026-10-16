FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY web ./web
RUN pip install --no-cache-dir '.[browser]' \
    && python -m playwright install --with-deps --only-shell chromium \
    && useradd --uid 10001 --create-home app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data /home/app \
    && chmod -R a+rX /ms-playwright
USER app
ENV DATABASE_PATH=/app/data/assistant.sqlite3
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD ["python", "-m", "drone_assistant", "health"]
EXPOSE 8000
CMD ["python", "-m", "drone_assistant", "serve", "--host", "0.0.0.0"]
