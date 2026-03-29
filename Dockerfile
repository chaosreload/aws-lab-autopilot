FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/
COPY prompts/ prompts/
COPY templates/ templates/
COPY config/ config/

CMD ["python", "-m", "src.orchestrator.sqs_handler"]
