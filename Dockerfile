FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir "cryptography>=43,<49" "segno>=1.6,<2"
COPY . .
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "server.py"]
