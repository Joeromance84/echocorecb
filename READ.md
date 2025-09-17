# Access Node AI & GitHub Integration Stack

The Access Node is a production-ready Python application that integrates AI capabilities (via OpenAI) and GitHub operations with secure artifact storage and task execution. It provides a robust system for running Python and shell commands, querying AI models, cloning GitHub repositories, and storing artifacts with audit logging, all within a Dockerized environment.

## Features

- **Task Execution**: Run Python scripts and shell commands securely using `Executor`.
- **AI Integration**: Query AI models (e.g., OpenAI's `gpt-4o-mini`) via `AIProxy`.
- **GitHub Integration**: Clone repositories and create issues using `GitHubClient`.
- **Artifact Management**: Store, retrieve, and manage files with integrity checks and audit logging using `ArtifactManager`.
- **API**: Schedule tasks via a FastAPI server with a `/tasks` endpoint.
- **Auditability**: Persist task results and artifact metadata in a SQLite database.
- **Security**: Role-based access control and file integrity verification.
- **Dockerized**: Runs in a lightweight `python:3.11-slim` container with Poetry for dependency management.

## Project Structure