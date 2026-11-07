FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock .
COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --app-dir /app --host 0.0.0.0 --port 8000"]
