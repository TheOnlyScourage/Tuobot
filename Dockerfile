# Python 3.13: nextcord 3.x needs 3.12 or newer. Keep this version in step
# with .github/workflows/ci.yml and ruff.toml's target-version
# (tests/test_platform.py fails if they drift apart).
FROM python:3.13-slim

# Stream logs in real time (unbuffered stdout) so Railway's log viewer
# shows output as it happens rather than in delayed chunks.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# gettext / locales/ removed in Layer 5 — see core/locales.py.
# The translator is now a passthrough stub, so no build-time
# compilation step is needed.

CMD ["python3", "start.py"]
