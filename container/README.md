# Containerized UltraSinger

## Getting started

1. **Web UI (this fork):** from the repository root run `docker compose pull && docker compose up` (NVIDIA GPU) or `docker compose -f docker-compose.cpu.yml pull && docker compose -f docker-compose.cpu.yml up` (CPU). That uses `ghcr.io/binarygeek119/ultrasinger-with-webui:latest`. Then open http://localhost:8756. Details: [Docker](docker.md).
1. There are specific instructions for either Docker or Podman:
    1. [Docker](docker.md)
    1. [Podman](podman.md)

## Why run UltraSinger as a container?

Running UltraSinger in a container bears the following advantages

- Environment Consistency: Containers ensure that the application runs in the same environment across different machines, reducing the "it works on my machine" problem.
- Isolation: Containers isolate the application from the host system, preventing conflicts with other applications and dependencies.
- Simplified Deployment: Containers package the application and its dependencies together, simplifying the deployment process.
- Security: Containers provide an additional layer of security by isolating applications from the host system and each other.
