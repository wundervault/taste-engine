# Taste Engine — slim Streamlit deploy image.
# Built on Oracle alongside burnbox_wundervault_1.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app + data + scripts (everything needed at runtime)
COPY app.py /app/
COPY src/ /app/src/
COPY data/ /app/data/
COPY scripts/ /app/scripts/
COPY .streamlit/ /app/.streamlit/

EXPOSE 8501

# Streamlit headless, no usage stats, no CORS interference (Caddy handles TLS).
CMD ["streamlit", "run", "app.py", \
     "--server.headless=true", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--browser.gatherUsageStats=false"]
