FROM python:3.12-slim

WORKDIR /app

# Install both packages
# Note: In production, you might want to use a requirements file instead
# For local development with editable installs:
COPY . /app/

# Install core-integrations first (if in sibling directory)
# For local dev: pip install -e ../core-integrations
# For production: specify as a package requirement in pyproject.toml

RUN pip install --no-cache-dir -e .

EXPOSE 8080

CMD ["python", "-m", "collections_sync"]
