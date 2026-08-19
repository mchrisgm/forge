# Multi-stage build: node builds the PWA, the final stage carries only the
# static bundle at /app/dist. docker-compose.yml runs this image with
#   sh -c "rm -rf /srv/ui/* && cp -r /app/dist/. /srv/ui/"
# so the final stage must include a shell + cp (alpine) and the dist dir.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM alpine:3.20
WORKDIR /app
COPY --from=build /app/dist /app/dist
# Compose overrides this with the copy-to-volume command; standalone runs just
# prove the bundle is present.
CMD ["ls", "-la", "/app/dist"]
