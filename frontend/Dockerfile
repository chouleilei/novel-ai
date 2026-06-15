FROM node:22-alpine AS build

WORKDIR /app

COPY frontend/package.json /app/package.json
COPY frontend/package-lock.json /app/package-lock.json
RUN npm ci

COPY frontend /app
RUN npm run build

FROM nginx:1.27-alpine

COPY frontend/nginx.conf.template /etc/nginx/novel-ai/default.conf.template
COPY frontend/docker-entrypoint.d/10-novel-ai-auth.envsh /docker-entrypoint.d/10-novel-ai-auth.envsh
COPY frontend/.htpasswd /etc/nginx/.htpasswd
RUN chmod +x /docker-entrypoint.d/10-novel-ai-auth.envsh
COPY --from=build /app/dist /usr/share/nginx/html
