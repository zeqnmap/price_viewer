FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY templates templates
COPY static static
ENV DB_PATH=/data/listings.db PYTHONUNBUFFERED=1
EXPOSE 5000
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:5000", "main:app"]
