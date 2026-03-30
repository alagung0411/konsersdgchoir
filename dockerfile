# Gunakan Python 3.12 slim
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy semua file project
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set environment variable Flask
ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_ENV=production

# Expose port
EXPOSE 8080

# Jalankan Flask
CMD ["flask", "run", "--port", "8080"]