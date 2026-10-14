# Antibody Landscape Monitor

A full-stack monitoring pipeline for therapeutic antibodies. It ingests public
antibody and clinical-trial data, analyzes every heavy and light chain for
developability liabilities, detects what changed since the last run, and surfaces
those changes as **sourced signals** in a dashboard.

**Live demo:** https://antibody-landscape-monitor.vercel.app (read-only; the API may take ~30 s to wake up)

**Stack:** Python · Flask · SQLAlchemy · PostgreSQL · AWS Step Functions · S3 · Next.js · TypeScript

---

## Why

Competitive views of a therapeutic area go stale fast. Trials change status, new
programs appear, and assets advance a phase. This project treats that as a data
engineering problem. It snapshots the sources on a schedule, diffs each run against
the previous one, and records every change as a signal with its source attached.

On the scientific side, each antibody is analyzed **per chain**, so heavy and light
chain sequences are scanned separately for liabilities such as glycosylation motifs,
deamidation sites and unpaired cysteines.

## Features

- **Scheduled ingestion** from Thera-SAbDab and the ClinicalTrials.gov API v2
- **Raw snapshots** per run (S3 or local), so every run is reproducible and auditable
- **Multi-chain analysis**: heavy and light chains are processed in parallel and flagged for liabilities
- **Change detection** that diffs runs and emits typed signals (`TRIAL_STATUS_CHANGED`, `NEW_TRIAL`, ...)
- **Source-backed signals**: every change links to the record it came from
- **Dashboard** with target landscape, antibody detail, signal feed and run history

## Architecture

```
 EventBridge (daily)
        |
        v
 +--------------------- Step Functions ----------------------+
 |                                                           |
 |  [Fetch Thera-SAbDab]          [Fetch ClinicalTrials.gov] |
 |           \                          /                    |
 |            v                        v                     |
 |          S3 raw/<run_id>/  (immutable snapshots)          |
 |                        |                                  |
 |                        v                                  |
 |          [Normalize]  -->  Map: per chain                 |
 |                              |-- heavy: liabilities       |
 |                              |-- light: liabilities       |
 |                        |                                  |
 |                        v                                  |
 |          [Diff vs previous run]  -->  signals             |
 +------------------------|----------------------------------+
                          v
                 +-----------------+      +----------------+      +-------------+
                 |   PostgreSQL    | <--- |  Flask API     | <--- |  Next.js UI |
                 +-----------------+      |  (SQLAlchemy)  |      +-------------+
                                          +----------------+
```

Each pipeline step is a plain Python function (`dict -> dict`), the same contract
as a Lambda task. The pipeline runs locally with a single command or on AWS through
the Step Functions definition in `infra/`.

## Data model

```
 antibody 1---* chain 1---* liability
    |
    *---* trial 1---* trial_snapshot

 pipeline_run 1---* signal
```

| Table | Purpose |
|---|---|
| `antibody` | therapeutic name, target, format, clinical stage |
| `chain` | heavy or light chain sequence, CDR3 length |
| `liability` | motif, position, kind, severity |
| `trial` | NCT ID, sponsor, phase |
| `trial_snapshot` | trial state as seen in a given run |
| `signal` | what changed (old -> new), source URL, run, reviewed flag |
| `pipeline_run` | timing, status, row counts |

## Liability rules

| Liability | Pattern | Severity |
|---|---|---|
| N-linked glycosylation | `N[^P][ST]` | high |
| Deamidation | `NG`, `NS` | medium–high |
| Isomerization | `DG`, `DS` | medium |
| Unpaired cysteine | odd `C` count | high |
| Oxidation | `M`, `W` in CDR3 | low |

> These are intentionally lightweight regex rules with a heuristic CDR3 locator.
> Production tooling would use a proper numbering scheme (e.g. IMGT via ANARCI).

## API

```
GET  /api/landscape?target=PD-1     antibodies by target x phase
GET  /api/antibodies/<id>           detail, chains, liabilities, trials
GET  /api/signals?since=&kind=      signal feed, newest first
POST /api/signals/<id>/seen         mark a signal reviewed
GET  /api/runs                      pipeline run history
```

## Getting started

```bash
# 1. Database
docker compose up -d postgres

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head

# 3. Run the pipeline once
python -m pipeline.run

# 4. API
flask --app api run --port 5000

# 5. Frontend
cd ../frontend
npm install
npm run dev     # http://localhost:3000
```

## Project structure

```
backend/
  api/          Flask app, routes, schemas
  models/       SQLAlchemy models
  pipeline/     fetch, normalize, analyze, diff steps + local runner
  migrations/   Alembic
frontend/
  app/          Next.js App Router pages (landscape, antibodies, signals, runs)
infra/
  state_machine.asl.json   Step Functions definition
docker-compose.yml
```

## Roadmap

- [ ] One-line LLM summaries for each signal
- [ ] Roll back an entity to a previous snapshot
- [ ] Entity resolution between sources beyond INN name matching
- [ ] Proper IMGT numbering for CDR-aware liability scoring

## Data sources

- [Thera-SAbDab](https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/therasabdab), Oxford Protein Informatics Group
- [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api)

All data used is public. This project is a personal portfolio piece.
