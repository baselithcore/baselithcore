# API Integration Example

Demonstrates integration with external APIs including webhooks, rate limiting, and retry logic.

## Features

- **Webhook Receiver**: Handle incoming webhook events
- **External API Client**: Call third-party APIs with resilience
- **Rate Limiting**: Respect API rate limits
- **Retry Logic**: Exponential backoff for failed requests

## Quick Start

```bash
cd examples/api-integration
pip install -r requirements.txt
python main.py
```

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook` | POST | Receive external webhooks |
| `/fetch` | POST | Fetch from external API |
| `/github/repos` | GET | Demo: GitHub API integration |
| `/events` | GET | List received events |

## Configuration

Environment variables:

```bash
GITHUB_TOKEN=ghp_...  # For GitHub API demo
WEBHOOK_SECRET=...    # Webhook signature validation
```
