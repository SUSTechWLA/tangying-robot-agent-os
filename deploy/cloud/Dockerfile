FROM golang:1.26 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /out/fleet-control-plane ./cmd/fleet-control-plane

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/fleet-control-plane /usr/local/bin/fleet-control-plane
EXPOSE 8080
USER nobody
ENTRYPOINT ["fleet-control-plane"]
