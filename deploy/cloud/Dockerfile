FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /out/cloud-control-plane ./cmd/cloud-control-plane

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/cloud-control-plane /cloud-control-plane
EXPOSE 8080
ENTRYPOINT ["/cloud-control-plane"]
