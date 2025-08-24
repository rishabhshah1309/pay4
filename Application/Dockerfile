# Application/Dockerfile
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Upgrade pip first (helps with manylinux wheels)
RUN python -m pip install --upgrade pip

# Install Python deps first for better layer caching
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project
COPY . /app
RUN python manage.py collectstatic --noinput

# copy entrypoint
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

# optional: set as default entrypoint
ENTRYPOINT ["bash", "/app/docker/entrypoint.sh"]

# Non‑root user for safety
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "pay4.wsgi:application"]

