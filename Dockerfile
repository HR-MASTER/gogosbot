FROM python:3.10-slim
WORKDIR /app

# (1) key.json 복사
COPY key.json .

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

# (2) 환경변수 지정
ENV GOOGLE_APPLICATION_CREDENTIALS="/app/key.json"

CMD ["python", "bot.py"]
