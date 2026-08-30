# Secrets

One file per key, named exactly as below, containing only the key itself.
No quotes, no `NAME=` prefix, no trailing newline needed.

    secrets/gemini_api_key      https://aistudio.google.com/apikey
    secrets/finnhub_api_key     https://finnhub.io/register

For example:

    echo -n "AIza..." > secrets/gemini_api_key

These are mounted read-only into the backend at /run/secrets and read at
runtime. They are never copied into the Docker image and never appear in
`docker inspect` or the container's environment.

Everything here except this file is gitignored. Leaving the directory empty is
a valid, working configuration: Loom still ingests SEC filings, transcripts,
insider records and prices without any key.
