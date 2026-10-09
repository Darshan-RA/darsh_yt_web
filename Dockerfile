FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PORT=8000
EXPOSE 8000
VOLUME ["/app/data", "/app/backups"]
CMD ["python", "server.py"]
