FROM python:3.12-slim

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
RUN pip install --no-cache-dir -r requirements.txt gunicorn whitenoise

# Copy Django project (FilaTrucking folder becomes /app/FilaTrucking)
COPY FilaTrucking/ ./FilaTrucking/

# Change to Django project directory
WORKDIR /app/FilaTrucking

# Collect static files
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "FilaTrucking.wsgi:application", "--bind", "0.0.0.0:8000"]