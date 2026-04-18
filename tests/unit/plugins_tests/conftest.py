"""Shared test env for baselithbot plugin tests.

The plugin now fails closed on dashboard write endpoints and inbound
webhooks when their auth secrets are not configured. Opt into the
documented insecure dev mode here so existing unit tests continue to
exercise write paths without each test supplying a bearer token or
signature header.
"""

from __future__ import annotations

import os

os.environ.setdefault("BASELITHBOT_DASHBOARD_ALLOW_INSECURE", "1")
os.environ.setdefault("BASELITHBOT_INBOUND_INSECURE", "1")
