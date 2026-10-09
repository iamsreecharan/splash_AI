# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
# A managed cloud build can mount its combined CA bundle. Ordinary local builds
# use the image's normal CA store. TLS and dependency hashes remain verified.
RUN --mount=type=secret,id=ca_bundle \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    python -m pip install --no-cache-dir --require-hashes -r requirements.txt
COPY app ./app
# Build contexts may contain mode-600 generated source. Application code is
# public within the image; mounted private keys retain their original modes.
RUN chmod -R a+rX /app/app && \
    groupadd --gid 10001 splash && useradd --uid 10001 --gid splash --no-create-home splash
USER splash
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
