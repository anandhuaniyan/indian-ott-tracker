FROM node:22-alpine AS frontend
WORKDIR /web
COPY frontend/package.json ./
RUN npm install --omit=dev
COPY frontend ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /web/dist ./frontend_dist

CMD ["sh", "-c", "mkdir -p media storage && chmod -R a+rwX media storage && alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
