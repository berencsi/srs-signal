# SRS Signal

**An AI Early-Warning System for Systemic Institutional Dysfunction**

SRS Signal is a research prototype for examining whether institutional
decisions are documented, reconstructable, and auditable, and whether similar
accountability weaknesses recur across different public institutions.

> We do not ask AI whether a system is democratic. We measure whether public
> power can explain, document, and correct itself.

## Public demonstration

- Application: https://srs-signal-jrswu9u7gkrdtzxyc3tucy.streamlit.app/
- Repository: https://github.com/berencsi/srs-signal

## What the prototype demonstrates

The deterministic application provides four pages:

- Analyze Decision
- Human Review
- Reviewed Audit Profile
- Systemic Signals

It demonstrates exact-source evidence verification, explicit human review, and
transparent recurrence indicators across three wholly fictional institutional
decisions.

The prototype does not determine legal correctness, unlawfulness, democratic
quality, or proven systemic dysfunction.

## Codex and GPT-5.6

OpenAI Codex using GPT-5.6 supported implementation, architecture review, test
design, debugging, and submission hardening.

The public application runtime is deterministic. It makes no live OpenAI API
call and requires no API key.

## Run locally

Python 3.12 or 3.13 is required.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
.venv/bin/streamlit run streamlit_app.py
```

## Intellectual property

Copyright © 2026 Béla Berencsi. All rights reserved.

This repository is not released under an open-source licence. Public access
does not grant general permission to copy, modify, redistribute, sublicense, or
commercially exploit its contents.

SRS Signal is part of the Self-Reflective Society Research Programme.
