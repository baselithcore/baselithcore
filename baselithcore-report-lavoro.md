# BaselithCore: consolidamento installazione Docker e plugin

Data: 14 settembre 2026

Branch iniziale: `core-ale`

Branch finale: `core-ale-main-integration`

Base integrata: BaselithCore `0.33.0` (`origin/main`, commit `76f87555`)

## Obiettivo

Rendere prevedibile l'installazione del Core e dei plugin. Il developer deve
avere Python 3.12+, Git e Docker con Compose; dipendenze Python, Node, package
manager e build frontend dei plugin vengono gestiti da BaselithCore.

Il flusso raggiunto e':

```bash
baselith setup docker-core
baselith plugin add <repository-plugin> --docker
```

Il Core puo' anche partire da solo, senza plugin, ed espone `/health`.

## Problemi iniziali

- Env locale e Docker poco distinti causavano variabili mancanti, password
  PostgreSQL incoerenti e collegamenti ai servizi sbagliati.
- Checkout diversi potevano condividere lo stesso progetto Compose e raggiungere
  container gia' avviati da un'altra cartella.
- Avvio, migrazioni, dipendenze e diagnostica richiedevano passaggi manuali.
- Le dipendenze Python installate sull'host o nel container corrente sparivano
  alla ricreazione del container.
- I frontend plugin richiedevano Node e npm/pnpm sul computer host.
- Il manifest descriveva parte dei requisiti, ma non era ancora il contratto
  completo usato dall'installer Docker.
- Lo scaffold router dichiarava un health endpoint non allineato alle route.
- Plugin opzionali mancanti potevano impedire l'avvio del Core secco.

## Modifiche e benefici

### Setup ed env

`baselith setup docker-core` prepara:

- `.env` per CLI e processi Python locali;
- `configs/.env.docker.core` per lo stack Docker Core;
- `DB_PASSWORD`, `SECRET_KEY` e gli altri valori richiesti;
- `COMPOSE_PROJECT_NAME`, derivato dal checkout per isolare installazioni diverse.

Il setup conserva i valori gia' validi. `doctor --fix` resta uno strumento di
diagnostica e riparazione host, ma non e' piu' un prerequisito del percorso Docker.

Beneficio: un checkout pulito riceve una configurazione coerente e non eredita
container, porte o password di un'altra installazione.

### Runtime Docker Core

E' stato aggiunto `docker-compose.core.yml` con API, PostgreSQL/pgvector, FalkorDB
e Qdrant. L'entrypoint API attende i servizi ed esegue le migrazioni Alembic. Il
Dockerfile installa i requirements generati dai plugin e usa `pip check` per
rilevare incompatibilita'.

`docker compose up` avvia gia' BaselithCore nel container: non serve eseguire
anche `baselith run` sull'host.

Beneficio: Core e migrazioni partono con una sola sequenza riproducibile.

### Doctor, preflight e Core secco

`doctor.py` e' stato suddiviso in controlli per ambiente, servizi e plugin. I
messaggi distinguono problemi host da problemi Docker. Il preflight segnala i
servizi indisponibili senza bloccare per default i casi recuperabili.

Fallback per router e document source opzionali permettono al Core secco di
partire senza plugin legacy. Il risultato e' una diagnosi piu' chiara e con meno
falsi blocchi.

### Installer generico dei plugin

Il nuovo comando e':

```bash
baselith plugin add <repository> --docker
```

Esegue clone, validazione manifest, controllo versione Core, risoluzione delle
dipendenze plugin, generazione di `configs/plugin-requirements.txt`, build
frontend in un container Node temporaneo, rebuild dell'immagine API, avvio e
health check.

Le dipendenze Python non vengono piu' installate con `docker compose exec api pip
install`: entrano nell'immagine e sopravvivono alla ricreazione dei container.
Node e pnpm/npm non sono richiesti sull'host.

`baselith plugin sync --docker` riallinea frontend, requirements e runtime per
tutti i plugin abilitati. Docker riusa i layer invariati; fingerprint pienamente
selettivi restano un miglioramento futuro.

### Manifest come contratto

`min_core_version`, `python_dependencies`, `plugin_dependencies` ed
`environment_variables` esistevano gia'. Il cambiamento importante e' averli
standardizzati nello scaffold e resi operativi nel flusso Docker.

Il contratto attuale comprende:

```yaml
name: example
version: 1.0.0
min_core_version: 0.33.0
entry_point: plugin:ExamplePlugin
python_dependencies: []
plugin_dependencies: []
environment_variables: []
frontend: null
health_endpoint: /example/health
```

Se esiste un frontend, `frontend` dichiara directory, package manager, comando di
build e output. `health_endpoint` indica la rotta che deve restituire HTTP 200.
`entry_point` e' la forma canonica; `entrypoint` resta accettato per compatibilita'.

Il motivo e' architetturale: il Core non deve conoscere doCheck, WikiGen o altri
plugin specifici. Ogni plugin descrive cio' che gli serve e l'installer applica
lo stesso processo generico. Un nuovo plugin conforme potra' essere integrato
senza aggiungere logica dedicata nel Core.

Le env dichiarate sono configurazione, non segreti inventabili dal Core. Quando
esiste `.env.example`, l'installer prepara `.env`; API key e credenziali reali
restano responsabilita' dell'operatore.

### Scaffold plugin

`baselith plugin create <nome> --type router|agent|...` genera manifest e
struttura iniziale conformi. Per `router` sono stati corretti il doppio prefisso
delle route e l'allineamento dell'health endpoint.

Beneficio: un plugin minimo creato da zero usa lo stesso percorso di un plugin
applicativo completo.

## Verifiche effettuate

Il giro finale e' stato eseguito da un clone pulito del branch di integrazione,
con Python 3.12 e virtualenv separato:

- installazione editable della CLI completata;
- setup, build e avvio Core completati;
- API, PostgreSQL, FalkorDB e Qdrant healthy;
- `GET /health`: HTTP 200;
- creazione e sincronizzazione del router `e2etestplugin`: completate;
- route e health del plugin di prova: HTTP 200;
- installazione doCheck da `chore/standardize-manifest`: completata;
- auth, requirements Python e frontend pnpm gestiti dal flusso;
- `GET /docheck/`: HTTP 200;
- seconda sincronizzazione con entrambi i plugin: completata.

Sono presenti test mirati per setup, env, manifest, compatibilita', workflow
Docker, scaffold e fallback. Pre-commit, CI e docs-sync completano i controlli
prima della Merge Request.

## Punti critici e limiti

- Il checkout Core e' ancora necessario; una distribuzione basata sulla sola
  immagine pubblicata e' un lavoro successivo.
- I plugin condividono il Python dell'API. Requisiti incompatibili devono essere
  corretti nei manifest.
- I requirements sono ricostruibili, ma non tutti bloccati a versioni esatte.
- Le dipendenze plugin mancanti seguono `baselithcore/plugin-<nome>` sul branch
  predefinito. Correzioni presenti solo su branch dedicati devono essere
  pubblicate o mergiate.
- `Plugin ready` certifica caricamento e HTTP 200, non login, LLM o servizi esterni.
- L'installer esegue codice e build del repository: vanno usati plugin fidati.
- `sync --docker` beneficia della cache Docker, ma non evita ancora ogni build
  tramite fingerprint completi.

## Preparazione al merge

Il lavoro e' nato su `core-ale` con commit dedicati a setup, env, doctor,
scaffold, Git onboarding, installer Docker, manifest, health check e test. Le
correzioni ai manifest di doCheck e auth sono rimaste nei rispettivi branch
plugin; il Core non contiene logica specifica per quei prodotti.

Nel frattempo `main` e' avanzato alla release `0.33.0`. E' stato creato
`core-ale-main-integration`, dove `main` e `core-ale` sono stati uniti, i
conflitti di runtime Docker e pre-commit sono stati risolti e `plugin sync
--docker` e' stato aggiunto.

Il branch finale conserva le novita' di `main` 0.33.0 e il nuovo percorso di
installazione. Prima della Merge Request vanno completati gli aggiornamenti
richiesti da docs-sync, eseguiti pre-commit/CI su un diff pulito e ripetuta la
prova definitiva senza artefatti e2e nel working tree.
