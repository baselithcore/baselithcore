# BaselithCore: installazione e plugin

Aggiornamento: 13 settembre 2026. Branch Core: `core-ale`.
Base del consolidamento: `0f0f877c`; le correzioni descritte sotto sono nel working tree.
Questa revisione non integra `main` e non controlla nuove release remote.

## Stato attuale

Il percorso di installazione e' `baselith setup docker-core`, seguito da
`baselith plugin add <repository> --docker`. La CLI prepara i file env,
coordina dipendenze, build frontend, immagine API, avvio e verifica HTTP.
L'host richiede Python per la CLI, Git e Docker con Compose; Node non serve.

La precedente prova isolata con doCheck ha restituito HTTP 200. Usava pero'
manifest corretti da repository locali: non certifica il percorso remoto
definitivo. La prova finale da clone e volumi nuovi resta rinviata.

Guida operativa: [Installazione Docker Core](mkdocs-site/docs/getting-started/docker-core.md).
Le revisioni precedenti di questo report restano consultabili nella storia Git.

## Problemi risolti e valore aggiunto

| Prima | Ora | Beneficio |
| --- | --- | --- |
| Env mancanti e indirizzi host/container confusi | Setup prepara profilo locale e Docker | Meno configurazione manuale |
| Password locale ereditata dalla CLI sovrascriveva quella Compose | L'installer applica i valori del file Docker | PostgreSQL e API ricevono credenziali coerenti |
| Placeholder lungo di SECRET_KEY non riconosciuto | Setup riconosce anche `__CHANGE_ME...` | Generazione del segreto su checkout nuovo |
| Cartelle di prova riusavano lo stesso namespace Compose | Nome progetto derivato dal checkout, se assente | Minore interferenza tra installazioni |
| Migrazioni e avvio gestiti separatamente a mano | Entrypoint Docker attende i servizi ed esegue Alembic | Primo avvio coordinato |
| Pacchetti installati sul computer o nel container corrente | Requirements dai manifest installati nell'immagine | Dipendenze conservate alla ricreazione |
| Frontend da costruire con Node locale | Builder Node temporaneo in Docker | Nessuna toolchain frontend richiesta all'host |
| Core bloccato dall'assenza di plugin legacy | Fallback per status e document sources | Avvio Core senza quei plugin |
| Diagnostica poco distinta dagli errori applicativi | Doctor e preflight dedicati | Cause piu' riconoscibili |

`Dockerfile-slim` e' il nome del Dockerfile gia' esistente: non e' stata
realizzata l'ulteriore versione alleggerita discussa. Lo stack Core avvia API,
PostgreSQL/pgvector, FalkorDB e Qdrant; non avvia Ollama, worker, sandbox o collector.

## Consolidamento di questa revisione

- `plugin add --docker` evita i controlli dei pacchetti Python e delle env
  nell'host, anche durante la preparazione delle dipendenze plugin. I controlli
  statici restano attivi; il runtime viene controllato dopo la build.
- I limiti minimo e massimo del Core usano le convenzioni di versione esistenti.
  Un limite invalido o incompatibile ferma l'installazione; l'assenza di limiti
  produce un avviso, non una falsa conferma di compatibilita'.
- Validazione statica di requisiti Python, nomi e vincoli delle dipendenze plugin,
  configurazione frontend e percorso health prima dell'abilitazione principale.
- Frontend dichiarati dalle dipendenze costruiti prima del principale, una volta
  per esecuzione. Le dipendenze legacy senza dichiarazione conservano gli asset
  distribuiti. Cicli e versioni incompatibili interrompono il percorso.
- `frontend: false` disabilita anche il rilevamento automatico. L'output deve
  essere una directory contenente almeno un file.
- Requirements generati in ordine stabile, senza duplicati, preferendo YAML
  come il loader. Manifest invalidi non vengono ignorati silenziosamente.
  Un file invariato non viene riscritto; la sostituzione avviene tramite file temporaneo.
- Conferma dell'installazione Python solo dopo la build riuscita.
  `pip check` nel Dockerfile rileva incompatibilita' dichiarate tra pacchetti.
- Probe HTTP sulla stessa porta configurata per Compose, anche quando presente
  solo nel file env; timeout monotono e pausa anche per status inattesi.
  Proxy dell'host e redirect non vengono seguiti: un redirect non prova la salute.
- La conferma distingue endpoint HTTP raggiungibile da funzionamento applicativo.
- `--force` rifiuta la sostituzione se non puo' verificare lo stato Git.
- Doctor usa eccezioni specifiche e chiude il socket anche in caso di errore.
- Host e porta interni del container restano definiti da Compose; rimossa la
  duplicazione nei default generati. Gli env gia' presenti vengono conservati.
- Il fallback document sources conserva il parametro `space_filter`.
  Corretti i tipi del riepilogo dipendenze e aggiornate le baseline per gli export
  gia' aggiunti, senza rimuovere API pubbliche.
- Test CLI riallineati a scaffold YAML e moduli doctor attuali, con ambiente isolato.
  Aggiunto un comando ripetibile di regressione per installazione e compatibilita'.

## Manifest ed env: il contratto reale

`min_core_version`, `python_dependencies`, `plugin_dependencies` ed
`environment_variables` esistevano gia'. Il valore aggiunto e' il loro uso
operativo nell'installer e uno scaffold comune, non l'invenzione di questi campi.

`plugin create` genera `manifest.yaml` con metadati di installazione.
Il developer compila i requisiti reali. I manifest YAML, YML e JSON precedenti
restano supportati; non tutti i campi nuovi sono obbligatori.
La struttura installabile richiede ancora `plugin.py`: un `entrypoint`
arbitrario non implementa un nuovo sistema di caricamento.

`frontend` dichiara percorso, package manager, comando e output; `health_endpoint`
dichiara una rotta locale che deve rispondere 200. Senza rotta dichiarata,
l'installer prova `/<nome-plugin>/`. Per un plugin solo API va dichiarata una rotta adatta.

| File | Responsabilita' |
| --- | --- |
| `.env` | CLI e processi Python locali |
| `configs/.env.docker.core` | Stack Docker Core |
| `plugins/<nome>/.env` | Configurazione locale del singolo plugin |
| `configs/plugins.yaml` | Abilitazione e opzioni dei plugin, non segreti |
| `configs/plugin-requirements.txt` | File generato dai manifest abilitati |

I profili locale e Docker non sono identici. Le password possono differire;
per usare la CLI locale sul database Docker bisogna allineare esplicitamente
credenziali e porte. `doctor --fix` copia un template e crea directory:
non legge credenziali dal container. Il setup conserva i valori gia' validi.

Il plugin riceve il proprio `.env.example` come punto di partenza, se presente.
API key, servizi esterni e configurazione cliente non possono essere inventati.
Un 200 sulla pagina non prova che login, upload o LLM siano configurati.

## Modifiche ai plugin e prove precedenti

| Repository | Branch / commit | Modifica |
| --- | --- | --- |
| doCheck | `chore/standardize-manifest`, `5fbde67` | Solo manifest: versione 0.1.16, entrypoint, frontend pnpm in ui/out, health /docheck/ |
| auth | `fix/runtime-python-dependencies`, `e7e75ca` | Solo manifest: versione 3.18.1 e cinque dipendenze runtime |

Auth dichiara `pyotp`, `qrcode[pil]`, `webauthn`, `argon2-cffi` e
`httpx`, gia' richiesti dal suo codice. Non sono state modificate autenticazione,
route, RBAC o UI. La correzione e' nel branch dedicato, non nel main di auth.
Questa revisione non modifica sorgenti o manifest nei repository plugin.

WikiGen ha permesso di verificare il builder con `frontend/`, Vite e una
dipendenza frontend locale. La prova precedente sulla porta 8011 ha dato
200 per health e WikiGen, 404 per una rotta inesistente. Questo non certifica
lo stato del repository remoto o una generazione wiki completa.

Il 12 settembre, Core `3288286` su configurazione e volumi nuovi ha fallito
durante le migrazioni: la password locale contaminava Compose. Sono emersi
anche il placeholder SECRET_KEY e dipendenze auth mancanti.

Dopo le correzioni, la prova isolata documentata il 13 settembre ha ottenuto:

- `GET /health`: 200;
- `GET /docheck/`: 200;
- `GET /__missing_control__`: 404;
- API, PostgreSQL, FalkorDB e Qdrant healthy.

Usava le porte 8024/55444/56391/56345, doCheck locale e un redirect Git locale
verso auth corretto. Cache Docker e pip erano disponibili. Non e' una misura
dei tempi su un computer senza cache, ne' una prova remota end-to-end.

## Verifiche ripetibili

Nel virtualenv di sviluppo con le dipendenze di test installate:

```bash
python scripts/check_installation_workflow.py
```

Il comando verifica confini architetturali, dimensione file, eccezioni silenziose,
API pubbliche e test mirati su CLI, env, installazione, fallback e versioni.
Non richiede Docker, non cambia gli env reali e non sostituisce tutta la CI.
Il suo `--no-cov` riguarda la selezione mirata; la soglia globale resta invariata.

Verifiche eseguite con Python 3.12.14, pytest 8.3.4, Ruff 0.15.5 e mypy 2.3.0,
in un virtualenv temporaneo separato dall'ambiente di lavoro:

| Controllo | Esito |
| --- | --- |
| Gate installazione, inclusi quattro controlli statici | 157 test passati |
| Loader, signing, registry e attivazione plugin | 56 test passati |
| mypy Core | Passato, 837 file |
| Typing strict Core e plugin ufficiali previsti dal gate | Passato |
| Ruff su Core, script e test | Passato |
| Formattazione Python dei file modificati | Passata |
| Coerenza documentazione e collegamento moduli/documenti | Passati |
| Suite globale pytest | Non completata: raccolta fermata per dipendenze di test mancanti, tra cui schemathesis, hypothesis e qdrant-client |
| Ruff sull'intero checkout, inclusi plugin locali | 41 rilievi nel repository doCheck, non modificato qui |
| Suite applicativa plugin | Non superata; dettagli sotto |
| Build Docker aggiornata e prova definitiva remota | Rinviate, non eseguite |

La prima esecuzione dei test plugin ha ottenuto 125 pass, 7 skip, 3 failure e
2 errori prima dell'arresto. Sono emerse aspettative diverse sui ruoli doCheck,
un test che richiede doCheck abilitato nella configurazione locale, import auth
incompleti e fixture HTTP bloccate dal sandbox. Dopo aver completato le dipendenze
nel virtualenv e consentito le fixture locali, la suite applicativa e' stata
interrotta dopo circa 156 secondi, con un solo test completato. Una selezione
breve ha poi raggiunto il timeout nel pool PostgreSQL del bridge auth.

Questi test dipendono anche dall'ambiente e non sono una prova isolata del solo
installer. Non sono stati modificati ruoli, configurazione reale o test plugin
per farli passare. Restano da preparare fixture isolate o un ambiente integrato
dedicato prima di dichiarare l'intero ecosistema verificato. Nessuno skip viene
contato come pass. La copertura globale non e' stata certificata.

La prova Docker pulita e' stata poi avviata su un checkout separato
`baselithcore-clean-final`. Il Core ha risposto su `/health`; `doCheck`, dal
branch `chore/standardize-manifest`, e' arrivato a `Plugin ready` dopo build
frontend e rebuild dell'immagine API. La prova con un router plugin creato da
zero ha evidenziato due punti: la dipendenza `auth` deve arrivare da un ref con
`python_dependencies` dichiarate, altrimenti manca `pyotp`; inoltre il template
router aggiungeva un doppio prefisso (`/api/<name>` + `/<name>`) mentre il
manifest dichiarava `/<name>/health`. Corretto il template, il comando
`baselith plugin add testplugin --docker` ha terminato con `Plugin ready` e
`/testplugin/health` ha risposto `200 {"healthy": true}` dentro il container.

Il controllo `pip check` e l'orchestrazione frontend sono quindi stati coperti
anche da una prova Docker reale; resta da ripetere la stessa prova dopo un clone
fresco del commit correttivo e con i riferimenti plugin definitivi pubblicati.

## Limiti e ordine dei prossimi passi

1. Chiudere gli esiti dei controlli estesi e revisionare questo diff prima
   di integrare `main`. Il gate `check_core_plugin_contract.py` citato nelle
   skill non esiste in questo branch; non e' stato dichiarato superato.
2. Rendere disponibili i manifest corretti dai riferimenti Git scelti.
   La risoluzione automatica usa ancora `baselithcore/plugin-<nome>` e il
   branch predefinito; se una dipendenza come `auth` ha il manifest corretto
   solo su un branch dedicato, quel ref deve essere pubblicato/mergiato oppure
   va aggiunto un meccanismo generico di ref per dipendenza.
3. Completare `sync --docker` con fingerprint e update/remove Docker uniformi.
   La stabilita' del file requirements e la cache Docker non equivalgono a questo:
   oggi le build vengono ancora invocate a ogni add.
4. Integrare `main` in un passaggio separato e ripetere il controllo mirato
   e la CI. I file piu' sensibili sono env/setup, installer, loader/shim,
   Dockerfile, requirements, scaffold e baseline delle API.
5. Eseguire alla fine la prova remota su checkout, env e volumi nuovi, poi
   verificare login e un'operazione doCheck reale. Verificare anche riavvio,
   ricreazione del container, conservazione dei dati e ripetizione dei comandi.

Le dipendenze Python sono ricostruibili dall'immagine, ma non tutte bloccate
a versioni esatte: non promettiamo build identiche nel tempo. I plugin
condividono l'ambiente Python dell'API e possono avere requisiti incompatibili.
Il builder esegue codice del repository installato: usare repository fidati.

Un errore non cancella automaticamente il clone e non ripristina la configurazione
precedente: l'installazione non e' transazionale. Non eliminare env o volumi per
riprovare; conservare credenziali e dati. Git conserva il codice, non database,
segreti, upload o immagini Docker.

Il percorso attuale richiede il checkout Core. Una distribuzione cliente con
immagine pubblicata, versionata e senza repository Core resta un lavoro separato.
