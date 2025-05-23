# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV POETRY_VERSION=1.5.1 # Or any recent stable version
ENV POETRY_HOME="/opt/poetry"
ENV POETRY_VIRTUALENVS_CREATE=false # Do not create virtualenvs inside the container
ENV PATH="$POETRY_HOME/bin:$PATH"

# Install Poetry
RUN apt-get update && apt-get install -y curl --no-install-recommends && rm -rf /var/lib/apt/lists/* \
    && curl -sSL https://install.python-poetry.org | python -

# Set the working directory in the container
WORKDIR /app

# Copy pyproject.toml and poetry.lock (if it exists)
# We copy these first to leverage Docker layer caching for dependencies
COPY pyproject.toml poetry.lock* ./

# Install project dependencies (excluding dev dependencies)
# --no-root is important if your project is a library, but for an app it's fine
# Using --only main if your poetry version supports it, or just `poetry install --no-dev`
RUN poetry install --no-dev --no-interaction --no-ansi

# Copy the rest of the application code
COPY ./src ./src

# Expose port 8080 (where Uvicorn will run)
EXPOSE 8080

# Command to run the FastAPI application
# This assumes your FastAPI app instance is named 'app' in 'src/model_service/app.py'
CMD ["poetry", "run", "uvicorn", "src.model_service.app:app", "--host", "0.0.0.0", "--port", "8080"]
