FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY benchmark ./benchmark
COPY examples ./examples
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data /app/sandbox /app/.secrets && chown -R appuser:appuser /app && chmod 0700 /app/.secrets
USER appuser
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
