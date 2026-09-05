# Guida italiana

Second Brain Agent Ecosystem collega una dashboard locale, il Librarian autonomo,
la ricerca semantica e i team Codex–Antigravity coordinati da Ruflo e CAO.
La versione 0.1 è una candidata al rilascio: consulta [le verifiche](../verification.md).

## Prima installazione

Servono macOS, Python 3.12+, Node 20+, npm, CAO con `cao-server`, tmux, Poppler e
Tesseract. Installa e autentica almeno Codex o il client AI Antigravity `agy`.
Con entrambi puoi ottenere una revisione fatta da un provider diverso dal writer.
L'installer non effettua login né acquista quota.

Se Antigravity avviato con `--print` segnala un permesso `command` negato, apri
`antigravity` in modalità interattiva nella cartella di progetto e valuta la
singola richiesta di autorizzazione. La modalità non interattiva non può
chiederti conferma; non serve disattivare globalmente le protezioni.

Con Homebrew e uv già installati:

```sh
brew install python@3.12 node tmux poppler tesseract
uv tool install 'cli-agent-orchestrator==2.5.0'
git clone https://github.com/Andrea-Giannuzzi/secondbrain-agent-ecosystem.git
cd secondbrain-agent-ecosystem
./install.sh
```

Scegli una cartella dedicata sotto la tua home, per esempio `~/Documents/SecondBrain`.
Tieni il clone Git separato dal vault. L'installer crea runtime privati, registra
MCP e skill globali e configura i servizi locali. I documenti dentro `00_Drop`
verranno elaborati dal Librarian: questo può usare quota AI e generare note.

Apri un nuovo terminale con `~/.local/bin` nel PATH, poi:

```sh
sbe doctor
sbe brain index
sbe dashboard open
```

Il primo comando controlla nomi, versioni e configurazioni senza mostrare segreti.
Il secondo inizializza l'indice locale; la prima volta scarica il modello di embedding.
Il terzo apre **http://legend.localhost:8765**. Funzionano anche `127.0.0.1` e `localhost`
sulla porta 8765. Per una prova usa le due note sintetiche in `examples/vault`.

## Uso quotidiano da VS Code o Terminale

Apri un progetto locale e il terminale nella sua cartella. Avvia `codex` oppure
`antigravity`. In VS Code, dopo l'installazione salva il lavoro, premi `⇧⌘P`, cerca
**Developer: Reload Window**, premi Invio e apri una nuova sessione agente.
Le sessioni già aperte possono conservare la configurazione precedente.

Le skill richiedono automaticamente il Second Brain quando servono conoscenze
precedenti e Ruflo per i task complessi. Vederle configurate non prova che siano
state usate: controlla le chiamate agli strumenti e i team nella dashboard.

Per richiederle esplicitamente nella chat:

| Scopo | Codex | Antigravity |
| --- | --- | --- |
| Consultare note | `$secondbrain-consult Cerca il concetto e cita i percorsi.` | `/secondbrain-consult Cerca il concetto e cita i percorsi.` |
| Team di lavoro | `$ruflo-team Investiga, implementa e revisiona: …` | `/ruflo-team Investiga, implementa e revisiona: …` |

Puoi inviare il primo prompt dal terminale:

```sh
codex -C "$PWD" '$ruflo-team Investiga, implementa e revisiona la richiesta seguente.'
antigravity --prompt-interactive '/ruflo-team Investiga, implementa e revisiona la richiesta seguente.'
```

Gli apici singoli impediscono alla shell di interpretare `$ruflo-team`.
Controlla che la skill compaia nel completamento del tuo client: la sintassi può
cambiare con la versione. Usa `/help` oppure chiedi in linguaggio naturale
“Usa la skill installata ruflo-team e i suoi strumenti MCP”. Non esiste un comando
universale `/ruflo` aggiunto da questo progetto. La ricerca nel Second Brain
non è una ricerca web: consulta le note locali attraverso il server `secondbrain`.

## Chi fa cosa

- Il provider della sessione è coordinatore e unico writer del progetto.
- Ruflo registra team, task, avanzamento e risultati revisionati.
- CAO esegue investigatore e revisore su copie redatte, senza autorizzarli a scrivere nel progetto.
- Il revisore preferisce il provider opposto al writer; se resta un solo provider,
  la dashboard indica la revisione come degradata.
- Il Librarian elabora autonomamente i documenti; il suo Executor deterministico
  applica le modifiche validate al vault, con transazioni e backup.
- Gli agenti di programmazione consultano il vault tramite MCP in sola lettura.

Quattro ruoli non significano quattro intelligenze attive contemporaneamente.
I sette profili correnti comprendono cinque profili Librarian e due worker Ruflo;
si installano quelli relativi ai provider presenti. Gli storici e quelli CAO integrati
vanno letti separatamente.

## Quota e ripresa

Il fallback di Librarian e worker CAO prova un provider alternativo ammesso.
Quando entrambi falliscono, i circuiti limitano i tentativi. Un errore di rete non
dimostra che la quota sia esaurita.

Se termina la quota del writer, apri l'altro provider nella stessa cartella e scrivi:

> Usa ruflo-team. Il writer precedente ha restituito un errore confermato di quota
> esaurita. Elenca i team attivi, prendi in carico quello relativo a questo progetto
> e continua le task aperte. Non interrogare il provider esaurito e indica come
> degradata ogni revisione effettuata dallo stesso provider del writer.

L'agente esaurito non può avviare da solo l'altra estensione. La ripresa è guidata:
tu avvii il client, Ruflo rende disponibile lo stato. Non chiedere takeover per
semplice lentezza o un errore generico.

## Comandi disponibili

```text
sbe doctor
sbe dashboard open
sbe dashboard status
sbe librarian status
sbe librarian run
sbe librarian pause
sbe librarian resume
sbe brain index
sbe brain search "concetto"
sbe brain evidence "concetto"
sbe brain read "30_Knowledge/Local Search.md"
sbe brain related "30_Knowledge/Local Search.md"
sbe team list
sbe team status TEAM_ID
sbe uninstall
```

`run` e `resume` possono consumare quota AI e produrre transazioni documentali.
`search`, `evidence`, `read` e `related` sono locali e non usano quota; il ragionamento
successivo dell'agente può usarla. Le prove sono estratti brevi redatti: allegati,
cartelle tecniche e Source riservate non sono leggibili attraverso questi strumenti.

## Leggere e controllare la dashboard

In alto trovi ecosistema, provider, team, ruoli, task e configurazione; sotto la
pipeline Librarian. “Attività rilevata” indica soltanto un file di sessione aggiornato.
“Ultima chiamata orchestrata” descrive un tentativo specifico. “Ultimo controllo
quota” vale nell'istante registrato: non garantisce la disponibilità adesso.
Un Librarian fermo con uscita regolare può semplicemente essere in attesa del timer.

Le impostazioni globali funzionano nei progetti locali, salvo restrizioni del client
o regole specifiche del progetto. SSH, container e altri host non ricevono automaticamente
questi runtime. In VS Code consulta il pannello del provider; per Antigravity verifica
il menu `… → MCP Servers`, se disponibile nella versione installata.
Skill Codex: `~/.codex/skills`; skill Antigravity: `~/.gemini/config/skills.json`;
policy Antigravity: `~/.gemini/GEMINI.md`. Non vengono aggiunti hook globali.

## Aggiornamenti e protezione dei dati

Per Andrea, `_System` resta la sorgente di sviluppo locale. L'esportatore copia
solo file approvati verso una working copy Git separata: GitHub serve alla distribuzione.
Le modifiche future si sviluppano e verificano localmente, poi si esportano e si
pubblicano come nuove versioni. Nessun download GitHub modifica automaticamente il vault.

Per migrare la vecchia installazione, ferma il lavoro attivo, controlla la copia
pubblica e usa `./install.sh --vault "$HOME/Documents/SecondBrain" --adopt-existing`.
Il comando adotta esplicitamente i vecchi runtime e profili, con backup privati.
Indici, code, note, transazioni e memoria Ruflo vengono preservati.

La disinstallazione ripristina i soli file gestiti se non sono stati modificati
nel frattempo. Se trova modifiche dell'utente si ferma senza cancellarle. Vault,
indici, ambienti di dipendenze e backup restano disponibili.

Non pubblicare output nativi di `antigravity mcp list`: possono includere chiavi
di altri MCP. Condividi `sbe doctor`; controlla manualmente ogni screenshot e log.
La redazione aiuta, ma non garantisce che un documento sia anonimo.

Per dettagli: [architettura](../architecture.md), [privacy](../privacy.md),
[risoluzione problemi](../troubleshooting.md), [migrazione](../migration.md).
