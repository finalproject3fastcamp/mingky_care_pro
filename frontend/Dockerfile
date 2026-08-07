FROM node:24-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

ARG VITE_API_BASE_URL=/api
ARG VITE_CAMERA_STREAM_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_CAMERA_STREAM_URL=${VITE_CAMERA_STREAM_URL}

RUN npm run lint && npm run build

FROM nginx:1.29-alpine

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
