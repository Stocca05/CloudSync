# Contributing to CloudSync ◈

Thank you for your interest in contributing to **CloudSync**! Whether you are reporting bugs, improving documentation, designing new UI features, or building support for more storage providers, your help is welcome.

---

## 🛠️ Development Setup

CloudSync is built with modern Python 3.12, FastAPI, PostgreSQL, and Rclone. We use [`uv`](https://github.com/astral-sh/uv) for fast, reproducible dependency management.

### 1. Prerequisites
- **Python 3.12+**
- **Rclone** (`brew install rclone` or `sudo apt install rclone`)
- **uv** (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **PostgreSQL** (or SQLite for local mock testing)

### 2. Clone and Install Dependencies
```bash
git clone https://github.com/Stocca05/CloudSync.git
cd CloudSync

# Install development dependencies
uv sync
```

### 3. Run the Development Server
```bash
# Start local API with auto-reload
uv run uvicorn cloudsync.api:create_app --factory --reload --port 8080
```

---

## 🧪 Testing & Code Quality

Before submitting a pull request, verify that all unit and integration tests pass cleanly and linting succeeds:

```bash
# Run pytest test suite
uv run pytest

# Check code formatting and linting
uv run ruff check cloudsync tests

# Format code
uv run ruff format cloudsync tests
```

---

## 🏗️ Architecture Guidelines

- **Zero Heavy Frontend Dependencies:** The web client is built with native modern ES6 JavaScript and responsive CSS (dark glassmorphism theme) without external JS frameworks (React/Vue/Webpack) or external CDN dependencies. This guarantees maximum security, fast load times, and full CSP compliance.
- **Fail-Safe Worker Architecture:** Worker nodes interact with the control plane via lease-based job claims and heartbeats. Jobs must never be dropped silently when a worker shuts down: they must be released gracefully (`/internal/jobs/{id}/release`).
- **Data Security:** Cloud provider credentials and access tokens are always encrypted at rest using Fernet symmetric encryption and never printed to console logs or written unencrypted to disk.

---

## 📬 Submitting a Pull Request

1. **Fork the repository** and create your branch from `main`:
   ```bash
   git checkout -b feature/amazing-feature
   ```
2. **Commit your changes** with clear messages following [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat:` new feature or capability
   - `fix:` bug fix
   - `docs:` documentation update
   - `refactor:` code reorganization without functional changes
   - `test:` adding or updating tests
3. **Push to your fork** and submit a Pull Request.

---

## 📄 License

By contributing to CloudSync, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).
