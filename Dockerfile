# API image: FastAPI served by uvicorn.
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shipsense shipsense
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
CMD ["uvicorn", "shipsense.api:app", "--host", "0.0.0.0", "--port", "8000"]
