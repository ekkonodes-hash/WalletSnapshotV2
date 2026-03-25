# Use the official Playwright + Python image — Chromium already included
FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY . .

# Create persistent-friendly directories
RUN mkdir -p outputs browser_profile

EXPOSE 5000

CMD ["python", "app.py"]
