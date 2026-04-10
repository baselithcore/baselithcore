# ADR 0001: Sacred Core Boundaries

## Status

Accepted

## Context

BaselithCore dichiara un'architettura `core/plugin/app`, ma il repository contiene ancora moduli legacy domain-specific nel `core/`, tra cui browser agents, scraping, document sources, goal tracking compat layer e router applicativi.

Questo ADR non risolve direttamente il debito architetturale esistente. Definisce invece il primo vincolo operativo: da ora in poi il debito non deve più crescere.

## Decision

Le seguenti regole diventano vincolanti:

1. `core/` non può importare `plugins/`, salvo shim di compatibilità esplicitamente allowlistati e schedulati per rimozione.
2. I path legacy `core/agents/`, `core/doc_sources/`, `core/goals/`, `core/routers/` e `core/scraper/` sono congelati.
3. Non possono essere aggiunti nuovi moduli Python sotto quei path senza una decisione architetturale esplicita.
4. Le nuove capability domain-specific devono nascere come plugin o application-layer code.

## Enforcement

Le regole sono verificate da:

- `scripts/check_architecture_boundaries.py`
- un job CI dedicato
- un hook locale in `pre-commit`

## Consequences

- Il repository continua a funzionare nello stato attuale.
- Le violazioni esistenti vengono trattate come baseline legacy, non come precedent da estendere.
- La migrazione fuori dal `core/` può avvenire in modo incrementale senza perdere controllo architetturale.
