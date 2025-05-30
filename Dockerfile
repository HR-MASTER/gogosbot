# Dockerfile

FROM python:3.10-slim
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY bot.py .

# Run the bot
CMD ["python", "bot.py"]
