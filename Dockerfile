# Use the official lightweight Python image
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY app_bunny.py .

# Tell the container where our persistent storage is going to be mounted
ENV PERSISTENT_STORAGE_DIR="/mnt/storage"

# Expose the port Uvicorn will run on
EXPOSE 8000

# Run the FastAPI application
CMD ["uvicorn", "app_bunny:app", "--host", "0.0.0.0", "--port", "8000"]