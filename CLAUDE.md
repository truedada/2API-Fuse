# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## High-Level Architecture

This project is a Python-based FastAPI application with a layered architecture:

*   **Framework**: FastAPI for building APIs.
*   **ORM**: Uses Tortoise ORM for asynchronous database interactions, configured via `aerich` for migrations.
*   **Database**: Primarily uses MySQL (indicated by `aiomysql` dependency).
*   **Caching/Messaging**: Integrates with Redis for caching and potentially other messaging patterns.
*   **Logging**: Utilizes `loguru` for structured logging.
*   **API Structure**:
    *   `app/main.py`: Main application entry point.
    *   `app/api/routers.py`: Defines API route inclusion.
    *   `app/api/v1/endpoints/`: Contains specific API endpoint implementations (e.g., `chat`, `admin`, `google_auth`).
    *   `app/api/deps.py`: Manages API dependencies and common utilities.
*   **Services**: `app/services/` holds the business logic and orchestrates interactions between repositories and adapters.
*   **Adapters**: `app/adapters/` provides interfaces for integrating with external services or different models (e.g., `openai`, `qwen`, `zai` for various LLM providers).
*   **Models**: `app/models/` defines database models using Tortoise ORM.
*   **Repositories**: `app/repositories/` abstracts database operations, providing a clean interface for data access.
*   **Schemas**: `app/schemas/` defines Pydantic models for request/response validation and serialization.
*   **Core Utilities**: `app/core/` contains core components like database setup, configuration, exception handling, and scheduler.

## Common Development Tasks

*   **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
*   **Run the Application**:
    The application can be started using uvicorn via `run.py`.
    ```bash
    python run.py
    ```
    This will start the FastAPI server, typically in development mode if `ENVIRONMENT` is set to "dev" in configuration.
*   **Run Database Migrations**:
    To apply database migrations using Aerich:
    ```bash
    aerich upgrade
    ```
    To create new migration scripts:
    ```bash
    aerich migrate
    ```
*   **Run API Test Script**:
    The `test.py` script contains an example of how to interact with the API, including signature generation for `zai` adapter.
    ```bash
    python test.py
    ```
    Note: This is an example API client script, not a comprehensive unit test suite.
