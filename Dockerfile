FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.11.19 && uv sync --locked --no-dev --python /usr/local/bin/python
COPY src src
COPY migrations migrations
COPY config config
COPY fixtures fixtures
COPY scripts/bootstrap_workflow.py scripts/bootstrap_workflow.py
USER 10001:10001
CMD ["/opt/venv/bin/python", "-m", "healthcare_dq.api.server"]
