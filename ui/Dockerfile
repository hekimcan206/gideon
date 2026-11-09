# syntax=docker/dockerfile:1.7
# Browser-mode build of the renderer for the docker compose demo (the desktop app is the Electron build).
FROM node:22-alpine AS build
WORKDIR /ui
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
RUN npm run build:renderer

FROM nginxinc/nginx-unprivileged:1.29-alpine AS web
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /ui/dist/renderer /usr/share/nginx/html
EXPOSE 8080
