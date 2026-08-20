FROM golang:1.26 AS build
WORKDIR /src
COPY . .
# Vendored dependencies (go mod vendor) make the cloud build hermetic and
# offline-safe; regenerate with: go mod vendor
RUN CGO_ENABLED=0 go build -mod=vendor -trimpath -o /out/fleet-control-plane ./cmd/fleet-control-plane \
 && CGO_ENABLED=0 go build -mod=vendor -trimpath -o /out/edge-worker ./cmd/edge-worker

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates bash \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/fleet-control-plane /usr/local/bin/fleet-control-plane
COPY --from=build /out/edge-worker /usr/local/bin/edge-worker
EXPOSE 8080 8443
USER nobody
ENTRYPOINT ["fleet-control-plane"]
