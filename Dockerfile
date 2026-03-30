FROM python:3.12-slim

WORKDIR /app

# Install AWS CLI v2
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/awscliv2.zip /tmp/aws \
    && apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/
COPY prompts/ prompts/
COPY templates/ templates/
COPY config/ config/

CMD ["python", "-m", "src.orchestrator.sqs_handler"]
