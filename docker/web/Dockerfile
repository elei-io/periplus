FROM node:24-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
COPY packages/atlas-console-core/package.json packages/atlas-console-core/package.json
COPY packages/atlas-terminal-shell/package.json packages/atlas-terminal-shell/package.json
COPY packages/atlas-web-shell/package.json packages/atlas-web-shell/package.json
COPY web/package.json web/package.json
RUN npm ci

COPY packages/atlas-console-core/ packages/atlas-console-core/
COPY packages/atlas-web-shell/ packages/atlas-web-shell/
COPY web/ web/
RUN npm run build:web
RUN find web/dist/assets -name '*.wasm' -exec gzip -9 -k '{}' ';'


FROM nginx:1.27-alpine

COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/web/dist/ /usr/share/nginx/html/

EXPOSE 80

HEALTHCHECK --interval=5s --timeout=3s --retries=12 --start-period=5s \
  CMD wget -q --spider http://127.0.0.1/api/openapi.json || exit 1
