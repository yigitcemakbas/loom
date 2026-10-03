# Secrets

One file per key, named exactly as below, containing only the key itself.
No quotes, no `NAME=` prefix, no trailing newline needed.

    secrets/gemini_api_key      https://aistudio.google.com/apikey
    secrets/finnhub_api_key     https://finnhub.io/register
    secrets/smtp_password       https://myaccount.google.com/apppasswords

Optional LLM providers. Each free tier is metered separately, so adding keys
multiplies the daily allowance rather than only adding redundancy. Any subset
works; providers with no key are skipped.

    secrets/cerebras_api_key    https://cloud.cerebras.ai
    secrets/groq_api_key        https://console.groq.com/keys
    secrets/mistral_api_key     https://console.mistral.ai/api-keys
    secrets/openrouter_api_key  https://openrouter.ai/keys

For example:

    echo -n "AIza..." > secrets/gemini_api_key

`smtp_password` is a Gmail app password, not the account password. Google stops
ordinary passwords from being used over SMTP, so an app password is the only
thing that works; generating one requires two-factor authentication on the
account. Google displays it as four groups of four letters, and the spaces are
presentational:

    echo -n "abcdefghijklmnop" > secrets/smtp_password

The matching non-secret settings (`SMTP_HOST`, `SMTP_USERNAME`, `SMTP_FROM`)
live in the project's `.env`. `SMTP_PASSWORD` must never be set as an
environment variable: it would take precedence over this file and be readable
through `docker inspect`.

These are mounted read-only into the backend at /run/secrets and read at
runtime. They are never copied into the Docker image and never appear in
`docker inspect` or the container's environment.

Everything here except this file is gitignored. Leaving the directory empty is
a valid, working configuration: Loom still ingests SEC filings, transcripts,
insider records and prices without any key.
