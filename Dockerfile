FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data
WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY claim_sync ./claim_sync
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home claimsync && mkdir /app/data && chown claimsync:claimsync /app/data
USER claimsync
EXPOSE 8000
CMD ["claim-sync", "web", "--host", "0.0.0.0"]
