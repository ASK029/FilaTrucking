FROM python:3.15-slim-bookworm

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies including pkg-config, MySQL client libraries, and WeasyPrint dependencies
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    pkg-config \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Django project (FilaTrucking folder becomes /app/FilaTrucking)
COPY FilaTrucking/ ./FilaTrucking/

# Change to Django project directory
WORKDIR /app/FilaTrucking

# collectstatic is NOT run at build time any more: settings.py refuses to start without SECRET_KEY/DATABASE_URL (QA-006),
# and in production the container collects into the shared ./FilaTrucking/staticfiles folder every time it starts.

EXPOSE 8000

CMD ["gunicorn", "FilaTrucking.wsgi:application", "--bind", "0.0.0.0:8000"]