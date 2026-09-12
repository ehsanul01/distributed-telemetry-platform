FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /worker .
FROM alpine:3.21
RUN apk add --no-cache ca-certificates
COPY --from=builder /worker /worker
USER 65532:65532
EXPOSE 8080
CMD ["/worker"]
