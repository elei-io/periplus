FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY packages/periplus-console-core/package.json packages/periplus-console-core/package.json
COPY packages/periplus-terminal-shell/package.json packages/periplus-terminal-shell/package.json
COPY packages/periplus-web-shell/package.json packages/periplus-web-shell/package.json
COPY packages/periplus-admin/package.json packages/periplus-admin/package.json
COPY packages/periplus-public/package.json packages/periplus-public/package.json
RUN npm ci
COPY packages/periplus-console-core/ packages/periplus-console-core/
COPY packages/periplus-web-shell/ packages/periplus-web-shell/
COPY packages/periplus-admin/ packages/periplus-admin/
RUN npm run build:admin

FROM nginx:1.27-alpine
COPY LICENSE NOTICE LICENSING.md THIRD_PARTY_NOTICES.md /usr/share/licenses/periplus/
ENV PERIPLUS_API_HOST=periplus-api PERIPLUS_API_PORT=8000
COPY docker/admin/nginx.conf /etc/nginx/templates/default.conf.template
COPY docker/admin/15-config.sh /docker-entrypoint.d/15-config.sh
COPY --from=build /app/packages/periplus-admin/dist/ /usr/share/nginx/html/
EXPOSE 80
HEALTHCHECK --interval=5s --timeout=3s --retries=12 --start-period=5s \
  CMD wget -q --spider http://127.0.0.1/healthz || exit 1
