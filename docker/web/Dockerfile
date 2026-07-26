FROM node:24-alpine AS build

WORKDIR /app

COPY package.json package-lock.json tsconfig.console.json ./
COPY atlas-cli/package.json atlas-cli/package.json
COPY packages/console-core/package.json packages/console-core/package.json
RUN npm ci --ignore-scripts

COPY packages/console-core/ packages/console-core/
RUN npm run build --workspace @atlas/console-core

WORKDIR /app/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build
RUN find dist/assets -name '*.wasm' -exec gzip -9 -k '{}' ';'


FROM nginx:1.27-alpine

COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/web/dist/ /usr/share/nginx/html/

EXPOSE 80

HEALTHCHECK --interval=5s --timeout=3s --retries=12 --start-period=5s \
  CMD wget -q --spider http://127.0.0.1/api/openapi.json || exit 1
