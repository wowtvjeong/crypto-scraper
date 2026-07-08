FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# DB가 저장될 디렉토리 (Railway/Fly 볼륨을 여기에 마운트)
RUN mkdir -p /app/data

EXPOSE 8811

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8811"]
