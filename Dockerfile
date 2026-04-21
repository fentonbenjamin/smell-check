FROM python:3.13-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8800
CMD ["python", "-m", "smell_check.gateway", "--port", "8800"]
