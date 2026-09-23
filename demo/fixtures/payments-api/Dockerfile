FROM python:3.12-slim
WORKDIR /app
# Copy only what the service needs: `COPY . .` would sweep in .env, .git and local secrets.
COPY src/ ./src/
# The service never needs root. The demo's one planted mistake is the token below.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin payments
USER payments
ENV PAYMENTS_API_TOKEN={{gen:base62:32}}
CMD ["python", "-m", "payments"]
