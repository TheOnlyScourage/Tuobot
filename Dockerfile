FROM python:3.11-slim

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
