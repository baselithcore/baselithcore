# BaselithCore: consolidamento Docker e plugin

Aggiornamento: 15 settembre 2026.

## Risultato

BaselithCore dispone ora di due flussi distinti che condividono lo stesso
runtime e lo stesso installer plugin:

1. sviluppo del Core da checkout, con `baselith setup docker-core`;
2. utilizzo e sviluppo plugin senza checkout del Core, con
   `baselith init <nome> --template docker-runtime`.

Il secondo flusso genera un progetto piccolo che usa l'immagine Core pubblicata.
Contiene soltanto Compose, configurazione, dati persistenti e plugin. Il codice
del Core rimane nell'immagine versionata.

## Problemi risolti

| Prima | Ora | Valore |
| --- | --- | --- |
| Env locali e Docker incompleti o discordanti | Setup genera profili coerenti e segreti validi | Primo avvio prevedibile |
| Stack diversi potevano condividere volumi e password | `COMPOSE_PROJECT_NAME` deriva dalla directory del progetto | Installazioni isolate |
| Migrazioni e avvio richiedevano passaggi manuali | Il container coordina startup e migrazioni | Core disponibile con Compose |
| Dipendenze plugin installate a mano nel container | Requirements generati dai manifest entrano nell'immagine | Ricreazione riproducibile |
| Node e package manager richiesti sull'host | Builder Node temporaneo in Docker | Host più semplice |
| Manifest letti ma non applicati a tutto il flusso | Compatibilità, dipendenze, frontend e health sono validati | Errori anticipati |
| Plugin nuovi richiedevano correzioni al routing health | Lo scaffold genera rotte coerenti col manifest | Plugin nuovo verificabile subito |
| Era necessario clonare l'intero repository Core | Il template `docker-runtime` usa l'immagine rilasciata | Progetto cliente più piccolo |

## Architettura attuale

L'immagine ufficiale contiene Core, CLI, runtime Python, migrazioni, modelli e
dipendenze standard. Il progetto generato monta dall'host:

- `plugins/` per installazione e sviluppo;
- `configs/` per registry ed env;
- `data/`, `documents/` e `logs/` per dati persistenti.

Il `Dockerfile` del progetto deriva da
`ghcr.io/baselithcore/baselithcore:<versione>` e aggiunge soltanto le dipendenze
Python dichiarate dai plugin abilitati. Docker riusa l'immagine Core: una modifica
ai plugin non ricostruisce torch, Chromium, modelli o il Core.

## Flusso senza checkout Core

Requisiti host: Python 3.12 o successivo per Baselith CLI, Git e Docker Compose.
Node, npm, pnpm e le dipendenze Python dei plugin non sono richiesti sull'host.

```bash
pip install baselith-core
baselith init my-core --template docker-runtime
cd my-core
docker compose --env-file configs/.env.docker.core -f docker-compose.core.yml up -d --build
curl --fail http://localhost:8000/health
```

Plugin remoto:

```bash
baselith plugin add <repository> --docker
```

Plugin locale nuovo:

```bash
baselith plugin create my-plugin --type router
baselith plugin sync --docker
```

## Contratto plugin

Il manifest dichiara identità, compatibilità Core, entrypoint, dipendenze Python,
dipendenze fra plugin, eventuale frontend e health endpoint. L'installer esegue:

```text
clone -> validazione -> compatibilità -> dipendenze -> frontend
      -> immagine derivata -> avvio API -> probe HTTP
```

Gli env restano separati per responsabilità:

| File | Responsabilità |
| --- | --- |
| `configs/.env.docker.core` | Configurazione e segreti dello stack |
| `configs/plugins.yaml` | Plugin abilitati e relative opzioni |
| `configs/plugin-requirements.txt` | Dipendenze Python generate dai manifest |
| `plugins/<nome>/.env` | Configurazione specifica del plugin |

La CLI non inventa API key o credenziali di servizi esterni. Un health check 200
certifica caricamento e raggiungibilità, non un flusso applicativo completo.

## Verifiche eseguite

La base `core-ale-main-integration` era già stata verificata con Core, doCheck e
un plugin router da zero. Sul branch Docker runtime è stata aggiunta una prova
senza checkout del Core:

- progetto generato in directory vuota;
- Compose validato;
- immagine derivata costruita da una Core image locale già verificata;
- API, PostgreSQL, FalkorDB e Qdrant avviati;
- `GET /health` restituisce 200;
- plugin `runtimetest` creato e sincronizzato;
- `GET /runtimetest/health` restituisce 200;
- il layer derivato senza nuove dipendenze è stato costruito in circa 0,1 s;
- 40 test mirati al runtime/plugin passati;
- 29 test di packaging e CI passati;
- Ruff passato sui file Python modificati.

## Limiti dichiarati

- Il template usa il tag della stessa versione della CLI. Il flusso pubblico è
  disponibile dopo pubblicazione coordinata di pacchetto Python e immagine GHCR.
- I requirements plugin sono dichiarativi ma non tutti fissati a una versione
  esatta; plugin incompatibili possono entrare in conflitto nell'ambiente comune.
- `plugin sync --docker` invoca ancora la build; Docker riusa i layer invariati,
  ma non è ancora presente un fingerprint applicativo completo.
- Update e remove Docker uniformi e rollback transazionale restano lavori futuri.
- Git conserva il codice, non volumi database, upload, segreti o immagini locali.

Guida operativa: [Docker Core and Plugins](mkdocs-site/docs/getting-started/docker-core.md).
