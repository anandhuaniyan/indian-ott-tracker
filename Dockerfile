FROM node:22-alpine AS frontend
WORKDIR /web
COPY frontend/package.json ./
RUN npm install --omit=dev
COPY frontend ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /web/dist ./frontend_dist

RUN groupadd --system --gid 10001 otttracker \
    && useradd --system --uid 10001 --gid otttracker --home-dir /app --shell /usr/sbin/nologin otttracker \
    && mkdir -p media private_uploads/report_issues \
    && chown -R otttracker:otttracker /app

USER 10001:10001

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*"]
