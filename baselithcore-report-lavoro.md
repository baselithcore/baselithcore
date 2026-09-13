# BaselithCore: report del lavoro su installazione e plugin

Report iniziale: 11 settembre 2026. Aggiornamento con prova pulita: 12 settembre 2026. Branch Core: `core-ale`, ultimo commit esaminato `3288286`. Branch doCheck: `chore/standardize-manifest`, commit `5fbde67`.

> **Esito aggiornato al 12 settembre:** la prova da clone, virtualenv e volumi nuovi ha completato installazione CLI, setup, clone dei plugin e build, ma il primo avvio e' fallito per credenziali PostgreSQL divergenti. Sono emersi anche un placeholder SECRET_KEY non sostituito e una dipendenza auth non dichiarata. Le verifiche positive delle sezioni 15-16 riguardano le prove precedenti e non certificano il primo avvio pulito. Dettagli e riproduzione nella sezione 20.

## 1. Obiettivo e risultato raggiunto

L'obiettivo era ridurre i passaggi manuali necessari per avviare BaselithCore e aggiungere plugin, mantenendo le convenzioni del progetto e repository separate per i plugin.

Abbiamo trasferito una parte consistente del runbook manuale nella CLI: preparazione dei file di configurazione, diagnostica, migrazioni, creazione e installazione dei plugin, build frontend in Docker e aggiornamento dell'immagine API con le dipendenze dichiarate.

Il risultato principale e' il percorso `baselith plugin add <repository> --docker`: con il Core predisposto, coordina l'installazione e verifica la raggiungibilita' HTTP del plugin. E' stato utilizzato nei test con doCheck; WikiGen ha consentito di verificare anche un frontend con struttura e build differenti.

Questo rappresenta un miglioramento concreto del flusso di sviluppo e installazione. Non equivale ancora a un installer universale per qualsiasi plugin o a una distribuzione cliente completamente autonoma dalla repository Core.

## 2. Il punto di partenza

Il runbook iniziale richiedeva di attivare Conda, clonare separatamente i plugin, installare pacchetti Python mancanti, correggere percorsi nel `.env`, avviare i servizi, applicare Alembic e controllare manualmente le pagine.

I problemi ricorrenti erano:

- Pacchetti mancanti, tra cui `pyotp`, `jinja2`, `greenlet`, `qdrant-client` e `sentence-transformers`.
- Confusione tra pacchetti installati sul computer e pacchetti disponibili nel container API.
- File `.env` assenti o con indirizzi e percorsi adatti a un ambiente diverso.
- Errori durante l'avvio poco immediati da distinguere dai problemi dei plugin.
- Frontend assenti perche' il clone non esegue la build.
- Migrazioni e disponibilita' del database da gestire a mano.
- Telemetria configurata senza un collector locale.
- Vecchi container e volumi che interferivano con le prove di una nuova installazione.

Il costo non era soltanto il numero di comandi: era dover ricostruire ogni volta quale componente mancava e in quale ambiente intervenire.

## 3. Diagnostica: miglioramenti a doctor

Abbiamo ampliato e organizzato i controlli di `baselith doctor`, separando i controlli generali da quelli dei plugin. Il comando espone stato, problema e indicazioni operative; supporta inoltre un output JSON.

I controlli comprendono interprete Python, Docker, ambiente, directory dati, dipendenze comuni, servizi configurati, telemetria, modalita' delle migrazioni e disponibilita' dei plugin e dei relativi frontend.

`doctor --fix` applica correzioni locali circoscritte: puo' creare il `.env` dal template disponibile e creare le directory dati, incluse `catalog` e `compliance`.

Valore aggiunto: i problemi vengono individuati piu' vicino alla loro causa, evitando di partire subito da un errore nel browser o da un log di startup molto lungo.

Precisazione: doctor eseguito sull'host diagnostica l'ambiente locale. Un warning su un pacchetto mancante in Conda non dimostra che quel pacchetto manchi nell'immagine Docker. Analogamente, un controllo di porta non certifica l'intero funzionamento di un servizio.

## 4. Configurazione: profili locale e Docker

Abbiamo introdotto helper comuni per preparare i profili e i comandi `baselith config env` e `baselith setup`.

Il profilo locale usa `.env`; il profilo Docker usa `configs/.env.docker.core`. La separazione e' necessaria: il processo locale raggiunge normalmente PostgreSQL su `localhost`, mentre il container API lo raggiunge tramite il nome di servizio `postgres`.

Il setup prepara valori predefiniti per directory, servizi, porte e opzioni di avvio. Usa i nomi di configurazione gia' presenti nel progetto, per esempio `CORE_DATA_DIR`, senza introdurre un secondo sistema parallelo.

L'implementazione genera credenziali casuali quando `DB_PASSWORD` o `SECRET_KEY` sono assenti o riconosciuti come placeholder. La prova del 12 settembre ha pero' dimostrato che il placeholder SECRET_KEY effettivamente presente in `.env.example` non viene riconosciuto: questo caso deve ancora essere corretto. Per `SECRET_KEY`, il profilo Docker puo' riutilizzare il valore locale gia' valido.

Abbiamo poi corretto `baselith setup docker-core` affinche' prepari entrambi i file: nella prima iterazione preparava soltanto quello Docker, lasciando la CLI locale senza `.env`.

Valore aggiunto: una nuova installazione richiede meno configurazione manuale e non dipende dai percorsi personali del computer originale.

Precisazioni importanti:

- I due file non sono identici e non vengono sincronizzati integralmente.
- In particolare, le password PostgreSQL dei due profili possono essere generate separatamente. Usare la CLI locale contro lo stesso database Docker richiede credenziali e porte coerenti.
- Il messaggio `already aligned` indica che l'helper non ha aggiunto o sostituito i valori previsti; non certifica che ogni impostazione esistente sia corretta.
- Il setup conserva molti valori gia' presenti: non bonifica automaticamente ogni percorso o configurazione storica errata.
- `doctor --fix` non estrae la configurazione da Docker: copia un template locale e prepara le directory. La generazione dei segreti appartiene agli helper di setup.
- Il setup Docker non coincide con tutti i controlli e le correzioni di doctor; non va descritto come un `doctor --fix` completo eseguito implicitamente.

## 5. Avvio locale e migrazioni

Abbiamo aggiunto `baselith db migrate`, che controlla preliminarmente PostgreSQL ed esegue `alembic upgrade head` con l'interprete Python della CLI, riportando l'esito.

Il profilo di sviluppo permette di coordinare preparazione dell'ambiente, avvio dei servizi Docker, attesa della disponibilita', migrazioni e diagnostica attraverso le opzioni di `baselith setup dev`.

Per lo sviluppo locale il profilo imposta `DB_MIGRATIONS_ON_STARTUP=false`, separando l'aggiornamento dello schema dall'avvio ordinario. Nel flusso Docker l'entrypoint esegue invece le migrazioni prima di avviare Uvicorn, salvo disabilitazione esplicita.

Sono due comportamenti intenzionali per due percorsi diversi. Non abbiamo implementato in questo lavoro un migration job di produzione separato.

Valore aggiunto: l'operazione sul database ha un comando riconoscibile e un esito esplicito; nel percorso Docker iniziale l'utente non deve ricordarsi di eseguirla a parte.

## 6. Preflight e correzione collegata alla issue 72

Abbiamo introdotto controlli prima dell'avvio tramite `baselith run`. Successivamente abbiamo corretto un comportamento troppo restrittivo: la mancata raggiungibilita' dei servizi non deve necessariamente impedire al processo di partire, dato che l'applicazione prevede anche inizializzazione differita.

Ora i problemi di raggiungibilita' riconosciuti dal preflight producono avvisi per impostazione predefinita; gli errori di configurazione classificati come bloccanti restano errori. L'opzione `--require-services` consente di richiedere esplicitamente servizi raggiungibili prima di avviare.

Valore aggiunto: il preflight aiuta la diagnosi senza introdurre un blocco anticipato incoerente con il comportamento previsto dall'applicazione.

Questo non rende facoltativa qualunque dipendenza: il runtime puo' comunque fallire se manca qualcosa di indispensabile. Inoltre, lo stack Docker Core continua ad attendere i servizi dichiarati healthy e a eseguire le migrazioni nel proprio entrypoint. La modifica a `baselith run` non elimina questi requisiti Docker.

## 7. Stack Docker dedicato al Core

Abbiamo aggiunto `docker-compose.core.yml`, il template di configurazione e un entrypoint dedicato. Lo stack comprende API, PostgreSQL con pgvector, FalkorDB per Redis/grafo e Qdrant.

L'API attende i controlli di salute dei servizi, prepara le directory principali, esegue le migrazioni e avvia Uvicorn. Plugin, configurazioni e directory operative vengono montati dalla cartella del progetto; i dati dei servizi hanno volumi persistenti.

Abbiamo aggiornato il Dockerfile utilizzato da questo profilo per includere l'entrypoint e installare le dipendenze Python generate dai manifest dei plugin. Abbiamo anche rimosso il Compose di sviluppo obsoleto.

Valore aggiunto: esiste un percorso Docker esplicito e ripetibile per il Core e per i plugin locali, senza ricostruire manualmente la composizione dei servizi.

Chiarimento sulla parola "slim": il profilo usa il file gia' chiamato `Dockerfile-slim`; non abbiamo realizzato l'ulteriore riduzione delle dipendenze discussa e poi rinviata. Lo stack Core esclude comunque worker, sandbox, observability e Ollama: non avvia tutti i servizi possibili del progetto.

`baselith setup docker-core` prepara i file e stampa il comando successivo; non scarica da solo l'immagine completa e non avvia lo stack. Download e build avvengono nei comandi Docker successivi. I tempi e lo spazio richiesti dipendono da cache, architettura, pacchetti e rete; non abbiamo misurato un tempo universale di installazione.

## 8. Vecchi volumi, password e porte

Durante le prove PostgreSQL rifiutava la password perche' un volume gia' inizializzato manteneva le credenziali precedenti. Modificare `POSTGRES_PASSWORD` nel file di configurazione non cambia automaticamente la password dentro un database esistente.

Abbiamo introdotto un `COMPOSE_PROJECT_NAME` derivato dal percorso del checkout quando il valore non e' presente. Nuove cartelle possono cosi' avere namespace e volumi Compose distinti, riducendo il riutilizzo involontario dei dati di un'altra prova.

Valore aggiunto: le installazioni di test in cartelle differenti sono piu' facili da distinguere e meno esposte a interferenze tra volumi.

Limiti: questa misura non ripara credenziali gia' incoerenti, non migra vecchi volumi e non libera le porte dell'host. Se si copia anche il vecchio file env con il nome progetto gia' impostato, quel nome viene conservato.

La curl che rispondeva prima di avviare il nuovo stack raggiungeva l'istanza precedente ancora sulla porta 8000. Per questo abbiamo usato porte alternative durante le prove parallele e corretto i probe dell'installer affinche' rispettino `BASELITH_HTTP_PORT` quando esportata nell'ambiente del comando.

## 9. Creazione di plugin locali

Abbiamo migliorato `baselith plugin create`: lo scaffold genera `manifest.yaml`, anche nel percorso interattivo, e puo' registrare il plugin in `configs/plugins.yaml`.

Il template propone i metadati comuni: identita', versione, versione minima del Core, entrypoint, dipendenze tra plugin, dipendenze Python, risorse richieste/opzionali, configurazione frontend, health endpoint e variabili d'ambiente.

Valore aggiunto: un plugin nuovo nasce con una struttura comune e puo' essere sviluppato localmente senza prima pubblicarlo su Git. Il developer deve comunque compilare il manifest con i requisiti reali e implementare le funzioni del plugin.

Creare lo scaffold non equivale ad aver costruito un'applicazione completa, ne' esegue automaticamente tutto il percorso Docker di installazione.

## 10. Manifest: cosa esisteva e cosa e' cambiato

`min_core_version`, `python_dependencies`, `plugin_dependencies` ed `environment_variables` esistevano gia' nei manifest esaminati. Non sarebbe corretto presentarli come invenzioni di questo lavoro.

Abbiamo uniformato lo scaffold e dato un uso operativo ai metadati nell'installer Docker. In particolare, la sezione `frontend` rende espliciti directory, package manager, comando di build e output; `health_endpoint` indica la rotta da verificare dopo l'installazione.

Valore aggiunto: le istruzioni necessarie a installare un plugin sono piu' vicine al plugin stesso e possono essere lette dal Core, invece di essere ricostruite manualmente dal developer.

La standardizzazione attuale non e' ancora uno schema rigoroso che impone tutti questi campi. Il codice mantiene compatibilita' con manifest precedenti e rilevamento automatico del frontend. L'installer richiede ancora la struttura con `plugin.py`: dichiarare un `entrypoint` arbitrario non rende installabile qualsiasi struttura Python.

## 11. Installazione da Git con Docker

Il comando `baselith plugin add <repository> --docker` coordina questi passaggi:

1. Clona il repository in `plugins/`, oppure riutilizza la directory gia' presente.
2. Legge il manifest, controlla la struttura di base e confronta la versione minima del Core quando dichiarata.
3. Crea il `.env` del plugin da `.env.example`, se disponibile e se il file locale non esiste.
4. Abilita il plugin e cerca di predisporre i plugin da cui dipende.
5. Raccoglie le dipendenze Python dichiarate dai plugin abilitati.
6. Esegue la build frontend con un container Node temporaneo.
7. Ricostruisce l'immagine API e avvia o ricrea il servizio tramite Compose.
8. Attende `/health` e verifica la rotta dichiarata dal plugin, oppure la rotta convenzionale.

Valore aggiunto: l'utente non deve eseguire separatamente installazione Python nel container, installazione Node sull'host, installazione frontend, build e riavvio.

La logica di build non contiene rami dedicati a doCheck o WikiGen. La risoluzione dei plugin mancanti usa pero' ancora la convenzione `baselithcore/plugin-<nome>` su GitHub: non e' un catalogo generale per dipendenze private o ospitate ovunque. Inoltre, le dipendenze tra plugin non ricevono ancora necessariamente l'intera orchestrazione Docker ricorsiva del plugin principale.

## 12. Dipendenze Python persistenti nell'immagine

Prima, installare un pacchetto in Conda non lo rendeva disponibile nell'API Docker. Installarlo manualmente con `docker compose exec api pip install ...` modificava il container corrente, con il rischio di perdere la modifica alla sua ricreazione.

Ora l'installer genera `configs/plugin-requirements.txt` dai manifest dei plugin abilitati; il Dockerfile lo usa durante la build. Le dipendenze diventano parte dell'immagine e restano disponibili nei container ricreati da quell'immagine.

Abbiamo escluso dal percorso operativo il tentativo di ricavare dipendenze cercando testo `pip install` nel codice: durante le prove produceva requisiti non validi. La fonte operativa e' il manifest.

Valore aggiunto: la configurazione delle dipendenze e' dichiarata e ricostruibile, non affidata a interventi manuali sul container.

"Riproducibile" qui significa che esiste una procedura dichiarativa di ricostruzione. Non significa build identiche byte per byte: requisiti con `>=`, dipendenze transitive e immagini non bloccate possono cambiare nel tempo. Inoltre, i plugin condividono l'ambiente Python dell'API: manifest corretti possono comunque richiedere versioni incompatibili tra loro.

## 13. Frontend automatico senza Node sull'host

Il Core avvia un container Node temporaneo, seleziona npm/pnpm/yarn secondo la configurazione o i lockfile, installa le dipendenze, esegue la build e controlla che la directory di output esista.

Sono supportate le directory frontend dichiarate nel manifest e il rilevamento di `ui/` o `frontend/`. Per i frontend sotto `plugins/`, il builder monta l'insieme dei plugin, cosi' da rendere disponibili dipendenze locali come `file:../../auth/ui`. Abbiamo aggiunto anche la preparazione delle dipendenze frontend locali e il percorso degli eseguibili `node_modules/.bin`.

Valore aggiunto: doCheck e WikiGen possono usare toolchain differenti senza richiederne l'installazione sul computer del developer.

Il plugin deve comunque fornire file, lockfile e comandi coerenti. Il controllo dell'output verifica l'esistenza della directory; non costituisce una verifica completa del contenuto o del comportamento della UI.

## 14. Variabili d'ambiente dei plugin

`environment_variables` elenca le variabili previste; `.env.example` ne documenta valori di esempio o default; `plugins/<nome>/.env` contiene la configurazione effettiva locale.

L'installer copia il template quando possibile, ma non inventa API key, credenziali o endpoint specifici del cliente. La sola dichiarazione nel manifest non implementa il consumo della variabile: il plugin deve leggerla secondo le convenzioni del progetto.

Valore aggiunto: i plugin mantengono la propria configurazione e un punto di partenza installabile. Per funzioni che richiedono servizi esterni resta necessario fornire valori reali.

## 15. Test WikiGen

WikiGen ha evidenziato esigenze generali utili per l'installer: frontend in `frontend/`, comandi Vite specifici e dipendenza frontend locale da auth.

Nel manifest locale sono stati esplicitati entrypoint, build frontend, directory `dist` e rotta `/wikigen/`. Le correzioni nel Core hanno riguardato il builder generico, le dipendenze locali, la generazione dei requirements e la disponibilita' di `SECRET_KEY`.

Durante la prova sulla porta alternativa 8011 abbiamo ottenuto HTTP 200 da `/health` e `/wikigen/`, con log di montaggio del frontend; una rotta inesistente restituiva 404.

Questo documenta l'avvio e la pubblicazione HTTP nella configurazione provata. Non dimostra che il repository WikiGen remoto abbia gia' ricevuto tutte le modifiche locali, ne' che sia stata completata una generazione wiki con tutti i servizi reali.

## 16. Test doCheck e modifica minima al manifest

Per la prova e' stato clonato doCheck standard. Nella cartella Core usata per questo test non era presente una precedente directory `plugins/docheck` da eliminare.

Il plugin originale dichiarava gia' dipendenze Python, auth, variabili d'ambiente e versione minima del Core. Mancavano i metadati espliciti di entrypoint, frontend e health endpoint.

Il primo tentativo ha costruito il frontend e l'immagine, ma l'avvio Compose ha incontrato una porta PostgreSQL gia' occupata. Spostando lo stack di prova su porte alternative, anche doCheck originale ha restituito HTTP 200: l'autodetection del Core riconosceva gia' la sua UI Next con export statico.

Successivamente abbiamo creato il branch `chore/standardize-manifest` e modificato soltanto `manifest.yaml`:

```yaml
version: 0.1.16
entrypoint: __init__.py
frontend:
  path: ui
  package_manager: pnpm
  build_command: pnpm build
  output_dir: out
health_endpoint: /docheck/
```

La versione precedente era `0.1.15`. Gli altri metadati sono rimasti presenti. Il valore della modifica e' rendere esplicito il contratto di installazione, senza intervenire sul codice applicativo del plugin.

Abbiamo verificato l'integrita' ed eseguito nuovamente il comando Docker completo nella configurazione di prova. L'installer ha terminato con `Frontend built`, `Plugin loaded`, `Health check passed` e `Plugin ready`; `/docheck/` ha restituito 200 e una rotta inesistente 404.

Il commit `5fbde67 chore(manifest): declare docker plugin runtime metadata` e' stato pubblicato nel branch dedicato. Questo non implica che sia gia' confluito nel main di doCheck.

La conclusione precisa e': installazione, build, caricamento e pagina HTTP di doCheck sono stati verificati. Non abbiamo documentato una prova completa di autenticazione, upload, analisi documentale con LLM e risultati. Anche la prova finale riutilizzava una directory plugin e un ambiente con attivita' precedenti: non e' una certificazione su macchina completamente vergine.

## 17. Verifiche e tracciabilita'

Durante il lavoro sono passati i controlli eseguiti di compilazione Python sui file modificati, confini architetturali, dimensione file e `git diff --check`. Per il preflight sono stati simulati casi di servizi irraggiungibili e configurazione LLM mancante. Per doCheck e' passato il controllo di integrita'.

Non va dichiarata una suite completa verde: il gate contrattuale richiamato dalle istruzioni locali non era presente; Ruff, mypy e pytest non erano disponibili nell'ambiente usato. I test HTTP sopra riportati sono quelli gia' eseguiti durante il lavoro, non una nuova esecuzione effettuata per scrivere questo report.

Commit principali sul Core, in ordine:

- `2032183`: setup Core e primo percorso Docker/plugin.
- `31edaae`: rimozione dello stack dev obsoleto.
- `f8444dd`: miglioramenti dello scaffold plugin.
- `da06fb3`: installazione plugin da Git.
- `f30d4ad`: orchestrazione dell'installazione Docker.
- `9bc32cf`: standardizzazione del flusso Docker e del template manifest.
- `9eab364`: propagazione di SECRET_KEY al profilo Docker.
- `80bec40`: correzione del preflight di avvio.
- `3288286`: rispetto della porta HTTP configurata nei probe.

Sono state fatte anche pulizie di file di cache TypeScript e regole Git per checkout locali e artefatti. I commit piu' recenti seguono i Conventional Commits richiesti; quelli precedenti non sono stati riscritti per uniformarne il titolo.

## 18. Cosa resta da completare rispetto all'obiettivo iniziale

Il percorso principale di aggiunta e' implementato, ma restano parti della richiesta complessiva:

- `plugin sync --docker` con fingerprint di manifest, requirements, lockfile e codice frontend, per evitare ricostruzioni non necessarie.
- Completamento uniforme di update e remove con aggiornamento del runtime Docker; non basta che esistano comandi di gestione con nomi simili.
- Validazione rigorosa dell'intero contratto manifest e diagnostica che distingua meglio host e container.
- Risoluzione completa delle dipendenze tra plugin, comprese repository esterne e build richieste dalle dipendenze.
- Lock delle versioni e gestione piu' esplicita dei conflitti tra dipendenze Python condivise.
- Un percorso distributivo per il cliente che non richieda il checkout Core e la build locale attualmente usati.
- Test automatizzato da ambiente davvero pulito e test applicativi completi dei plugin.

Il comando add riusa le directory esistenti e Docker puo' usare la propria cache. Questo non e' ancora l'idempotenza completa richiesta: il codice attuale richiama comunque la build frontend e la build API.

## 19. Valore aggiunto per developer e cliente

Per il developer, la differenza e' passare da una sequenza di installazioni e correzioni manuali a un flusso coordinato dalla CLI. I nuovi plugin hanno un template comune; quelli esistenti possono dichiarare come essere costruiti. L'host non deve avere Node e le dipendenze runtime dei plugin entrano nell'immagine API.

Per il cliente, abbiamo preparato una base piu' ordinata e trasferibile: configurazione generata, servizi definiti, dati persistenti, migrazioni esplicite e verifiche HTTP. Il packaging finale, il blocco delle versioni e le verifiche su installazione vergine rimangono il passo successivo.

Il risultato ottenuto e' una riduzione concreta dei passaggi manuali, con build e integrazione Docker coordinate dalla CLI. La prova pulita del 12 settembre dimostra pero' che non possiamo ancora dichiarare affidabile il primo avvio senza interventi: prima occorre correggere le criticita' della sezione seguente e ripetere il test. Il solo clone o il solo manifest non garantiscono il funzionamento integrale di qualsiasi plugin.


## 20. Prova da ambiente pulito del 12 settembre 2026

### Perimetro e isolamento

Abbiamo provato il codice Core committato, senza modificare il suo comportamento per far superare il test. E' stato creato un clone separato in `/private/tmp/baselithcore-clean-e2e-20260912`, con virtualenv nuovo in `/private/tmp/baselithcore-e2e-venv`, senza copiare file env, configurazione personale, database o cache frontend dalle installazioni precedenti.

Versioni provate:

- Core: `3288286`, branch `core-ale`, clonato dalla repository locale per testare esattamente il commit del report.
- doCheck: `5fbde67dc0e65f233daab946ec9d3ea05da584d8`, branch remoto `chore/standardize-manifest`.
- auth: `568f739ce732e6db7096310205ab140d483c12c6`, clonato automaticamente dall'installer.
- Docker Engine: `29.7.2`; virtualenv Python 3.12 su macOS ARM.

Il progetto Compose nuovo era `baselithcore-clean-e2e-20260912-a9c47ddc`. Docker ha creato nuovi volumi PostgreSQL, Redis e Qdrant. Le porte erano 8022, 55442, 56389 e 56343, distinte dagli stack gia' presenti.

La cache Docker e la cache pip del computer erano disponibili; diversi layer Core sono stati riutilizzati. E' quindi una prova di primo avvio con configurazione e dati nuovi, non una misura dei download su un computer senza cache. Il file `configs/plugins.yaml` del branch conserva inoltre alcuni plugin distribuiti gia' abilitati: il test ne ha mantenuto i default.

### Sequenza eseguita

Dalla cartella del clone, dopo la creazione del virtualenv:

```bash
/private/tmp/baselithcore-e2e-venv/bin/python -m pip install -e .
/private/tmp/baselithcore-e2e-venv/bin/baselith setup docker-core

env BASELITH_HTTP_PORT=8022 \\
  BASELITH_POSTGRES_PORT=55442 \\
  BASELITH_REDIS_PORT=56389 \\
  BASELITH_QDRANT_PORT=56343 \\
  /private/tmp/baselithcore-e2e-venv/bin/baselith plugin add \\
  https://github.com/baselithcore/plugin-docheck \\
  --ref chore/standardize-manifest --docker
```

Il riferimento al branch doCheck e' esplicito per provare il manifest standardizzato. Non abbiamo installato Node sull'host, eseguito pip manualmente nel container o alterato i manifest per aiutare la prova.

### Risultati osservati

| Passaggio | Esito | Evidenza |
| --- | --- | --- |
| Installazione CLI nel virtualenv nuovo | Riuscita | `pip install -e .` termina con codice 0 |
| Preparazione env | Comando riuscito, contenuto non completamente corretto | File creati; difetti descritti sotto |
| Clone doCheck e dipendenza auth | Riuscito | Entrambi scaricati dal comando add |
| Build frontend in Docker | Riuscita | Next completa la build; `ui/out` presente, circa 4,3 MB |
| Installazione dipendenze doCheck nell'immagine | Riuscita per i pacchetti verificati | `aiosqlite`, `aiofiles`, `langgraph`, `pdfplumber` presenti |
| Build immagine API | Riuscita | Immagine del progetto di prova creata |
| Avvio PostgreSQL, Redis e Qdrant | Riuscito | Tutti e tre healthy su volumi nuovi |
| Migrazioni e avvio API | Falliti | PostgreSQL rifiuta la password dell'API |
| Risultato installer | Fallito, codice 1 | `Docker API did not become healthy after plugin install.` |
| GET /docheck/ su porta 8022 | Non raggiungibile | curl codice 7, HTTP 000: nessun server disponibile |
| Operazione applicativa autenticata | Non eseguibile | API bloccata prima dell'avvio |

### Problema 1: contaminazione dell'ambiente Compose

La causa immediata non e' un volume vecchio. Il setup genera password PostgreSQL distinte per il profilo locale e quello Docker. Durante il percorso CLI, l'importazione di `core.config` carica il file locale in `os.environ`. L'helper `_compose` eredita l'ambiente del processo.

Il valore esportato viene usato per interpolare `POSTGRES_PASSWORD` del servizio PostgreSQL, mentre l'API riceve `DB_PASSWORD` dal proprio `env_file` Docker. Sono stati confrontati i valori senza stamparli:

- Password PostgreSQL uguale a quella del file locale: vero.
- Password PostgreSQL uguale a quella del file Docker: falso.
- Password API uguale a quella di PostgreSQL: falso.

L'API entra cosi' in un ciclo di riavvio durante Alembic, con `password authentication failed`. Il nome progetto univoco non puo' risolvere una discrepanza introdotta nello stesso avvio.

Correzione necessaria: definire e applicare una precedenza coerente per la configurazione Docker, evitando che valori locali caricati internamente dalla CLI prevalgano sull'env Docker. Verificare poi sia primo avvio sia riesecuzione con gli stessi volumi. Generare una password nuova a ogni tentativo non risolve il problema e rompe i database gia' inizializzati.

### Problema 2: SECRET_KEY resta quella di esempio

Il controllo `is_placeholder_secret` riconosce solo alcune stringhe esatte. Il placeholder piu' lungo usato da `.env.example` non rientra nell'elenco; viene conservato nel file locale e riutilizzato in quello Docker.

Il confronto ha confermato che entrambe le chiavi coincidono con quella del template. Non sono stati pubblicati valori segreti. Questo difetto rende errata l'affermazione generale che il setup generi sempre una nuova SECRET_KEY su clone pulito.

Correzione necessaria: riconoscere il placeholder realmente distribuito e verificare che installazioni nuove generino chiavi diverse tra loro, conservando invece le chiavi valide quando il setup viene ripetuto. La chiave di esempio non deve essere usata per una distribuzione reale.

### Problema 3: auth non dichiara pyotp

Il manifest auth scaricato non contiene `python_dependencies`. Il modulo `plugins/auth/mfa.py` importa `pyotp` e solleva esplicitamente ImportError se manca.

Un container temporaneo avviato dalla nuova immagine ha confermato: `pyotp` assente; `aiosqlite`, `aiofiles`, `langgraph`, `pdfplumber` e `greenlet` presenti. Quindi l'installazione Python dichiarata funziona, ma non puo' installare un requisito che il plugin dipendente omette.

Questo e' un ulteriore difetto accertato della composizione runtime, non la causa dell'errore PostgreSQL osservato. L'impatto finale sull'avvio di auth deve essere riverificato dopo lo sblocco delle migrazioni.

Correzione necessaria: completare il manifest auth con le dipendenze runtime effettive nel suo repository, mantenendo generico il Core.

### Diagnostica e prestazioni

Il comando ha mostrato anche controlli sulle dipendenze Python dell'host e molte variabili dichiarate come mancanti, pur proseguendo verso Docker. La distinzione tra requisiti obbligatori, default opzionali e ambiente controllato resta da migliorare. In particolare, auth senza `min_core_version` produce un warning seguito dal messaggio `Core compatible`: il testo e' piu' assertivo della verifica realmente eseguita.

Il contesto trasferito alla build era circa 709,48 MB. La cache generata in `plugins/.pnpm-store` occupava circa 563 MB e non e' esclusa dalla build context. Escluderla e' un miglioramento mirato per ridurre trasferimenti e contenuto non necessario nell'immagine.

### Chiusura e conclusione della prova

Sono stati fermati e rimossi solo i container e la rete del progetto di prova, senza `down -v`. I volumi, il clone e il virtualenv sono stati conservati per la diagnosi. Gli stack precedenti sulle porte 8000 e 8011 non sono stati modificati.

Non sono state apportate correzioni al Core o ai plugin durante questa prova. Non sono stati eseguiti commit o push. Questo aggiornamento documenta il risultato negativo, senza trasformare un intervento manuale in un falso successo del flusso automatico.

La priorita' successiva e' correggere l'isolamento della configurazione Compose e il riconoscimento del placeholder, completare il manifest auth, quindi ripetere il test su nuovi volumi. Solo dopo il superamento dell'avvio si potranno verificare login, operazioni documentali e comportamento senza/con servizi LLM reali. `plugin sync --docker` resta successivo a questa stabilizzazione.

## 21. Correzione stabilita e prova E2E del 13 settembre 2026

### Correzioni applicate dopo la prova negativa

Dopo il fallimento documentato nella sezione 20 sono stati applicati fix mirati, senza introdurre logica specifica per doCheck o WikiGen nel Core:

- Compose ora riceve i valori di `configs/.env.docker.core` per l'interpolazione delle variabili Docker. Questo evita che la `.env` locale caricata dalla CLI sovrascriva `DB_PASSWORD` del servizio PostgreSQL.
- Le porte e `COMPOSE_PROJECT_NAME` restano sovrascrivibili da shell. Questo permette test paralleli e ambienti isolati senza rompere la precedenza delle credenziali Docker.
- `is_placeholder_secret` riconosce anche placeholder lunghi che iniziano con `__CHANGE_ME`, incluso quello distribuito in `.env.example`.
- `.dockerignore` esclude `**/.pnpm-store`, riducendo il contesto Docker generato dalla build frontend.
- Gli shim legacy verso `plugins/api_routers` sono diventati opzionali. Se il plugin legacy esiste, il comportamento resta quello precedente; se manca, il Core non crasha.
- Il fallback del router status espone `/health` e `/health/ready` anche in Core pulito. Queste route sono contratti del Core e non devono dipendere da un plugin legacy.
- `core.doc_sources` ha un fallback minimale: se `plugins/document_sources` non e' installato, `create_document_sources()` restituisce lista vuota e il bootstrap indexing resta idle invece di generare traceback.
- Il manifest del plugin `auth` locale e' stato aggiornato con le dipendenze runtime Python effettive, inclusi `pyotp`, `qrcode[pil]`, `webauthn`, `argon2-cffi` e `httpx`.

Nota importante: il commit del plugin `auth` e' locale nel repository separato `plugins/auth`. Il push verso `https://github.com/baselithcore/plugin-auth` non e' stato eseguito perche' richiede approvazione esplicita su un secondo repository esterno e sul branch `main`.

### Prova E2E corretta

E' stata preparata una nuova copia pulita in `/private/tmp/baselithcore-clean-e2e-fix2-20260912`, escludendo solo cartelle locali root come `.git`, `.env`, `configs/.env*`, `plugins`, `data` e `documents`. La copia conserva invece il codice `core/plugins`, necessario al Core.

La prova ha usato:

- virtualenv temporaneo: `/private/tmp/baselithcore-e2e-fix2-venv`;
- porte isolate: API `8024`, PostgreSQL `55444`, Redis `56391`, Qdrant `56345`;
- doCheck clonato dal repository locale standardizzato;
- auth clonato tramite redirect Git locale verso il commit appena corretto, per evitare di dipendere da un push non autorizzato su `plugin-auth`.

Sequenza principale:

```bash
/private/tmp/baselithcore-e2e-fix2-venv/bin/python -m pip install -e .
/private/tmp/baselithcore-e2e-fix2-venv/bin/baselith setup docker-core

GIT_CONFIG_GLOBAL=/private/tmp/baselithcore-e2e-fix2-gitconfig \\
BASELITH_HTTP_PORT=8024 \\
BASELITH_POSTGRES_PORT=55444 \\
BASELITH_REDIS_PORT=56391 \\
BASELITH_QDRANT_PORT=56345 \\
/private/tmp/baselithcore-e2e-fix2-venv/bin/baselith plugin add \\
  /Users/alessandro/Developer/baselithcore/plugins/docheck \\
  --docker
```

### Risultato finale osservato

Il comando `baselith plugin add ... --docker` termina con:

```text
Plugin loaded
Health check passed
Plugin ready
```

Verifiche HTTP eseguite sullo stack temporaneo:

| Endpoint | Esito |
| --- | --- |
| `GET http://localhost:8024/health` | `200` |
| `GET http://localhost:8024/docheck/` | `200` |
| `GET http://localhost:8024/__missing_control__` | `404` |

Stato container:

- API healthy su `0.0.0.0:8024->8000`;
- PostgreSQL healthy su `127.0.0.1:55444->5432`;
- Redis healthy su `127.0.0.1:56391->6379`;
- Qdrant healthy su `127.0.0.1:56345->6333`.

Log filtrati dopo l'ultimo restart:

- `doCheck mounted at /docheck`;
- `Application startup complete`;
- `/health` risponde `200`;
- `/docheck/` risponde `200`;
- nessun traceback `plugins.document_sources`;
- nessun `ModuleNotFoundError` relativo a `plugins.api_routers`.

### Cosa e' stato dimostrato

Questa prova dimostra che, con Core corretto e manifest auth corretto, il flusso Docker plugin arriva al risultato richiesto:

```bash
baselith setup docker-core
baselith plugin add <repo-docheck> --docker
curl http://localhost:<porta>/docheck/
```

senza installare manualmente dipendenze Python, Node, pnpm/npm, dipendenze frontend o build frontend sull'host.

Le dipendenze Python del plugin entrano nell'immagine API tramite `configs/plugin-requirements.txt`; la build frontend avviene in un container Node temporaneo; il runtime Docker viene ricostruito e riavviato dalla CLI; il comando attende `/health` e poi verifica il plugin.

### Limiti ancora aperti

Resta da completare il flusso remoto puro:

- pushare il commit `auth` nel repository `plugin-auth`, oppure usare un branch dedicato e aggiornare la dipendenza plugin;
- ripetere il test clonando doCheck e auth solo da GitHub, senza redirect locale;
- implementare `plugin sync --docker` con fingerprint per evitare reinstall e rebuild quando nulla cambia;
- migliorare la diagnostica host/container: oggi `plugin add --docker` mostra ancora warning sulle dipendenze mancanti nel venv host, anche se poi il Docker runtime le installa correttamente;
- verificare operazioni applicative doCheck oltre alla pagina 200, per esempio login/setup e una chiamata API reale con auth.

Conclusione aggiornata: il blocco principale del primo avvio Docker e' stato risolto. Il Core pulito non dipende piu' rigidamente dai plugin legacy `api_routers` e `document_sources` per partire, e doCheck puo' essere integrato nel runtime Docker con il comando unico, purche' anche la sua dipendenza `auth` abbia manifest corretto e disponibile da Git.
