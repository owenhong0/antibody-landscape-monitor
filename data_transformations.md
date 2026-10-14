# Data Transformations

How raw Thera-SAbDab and ClinicalTrials.gov data becomes the clean records the
pipeline loads into Postgres, and why each step exists.

Code: `backend/pipeline/fetch.py`, `backend/pipeline/normalize.py`.

## The big picture

The pipeline has two kinds of steps, and they never mix:

- **Fetch** downloads raw data and saves it unchanged. No interpretation.
- **Normalize** reads those raw files and turns them into clean dicts shaped like
  our database tables. No network calls.

Neither step touches Postgres; a separate load step does that.

**Why split it this way?**

- The raw files are a record of exactly what each source said on the day of the
  run. If a normalization rule turns out to be wrong, we fix the rule and re-run
  normalize on the saved files. No re-download, and no guessing what the source
  looked like last week.
- Each step can be tested alone, with a small sample file and no network or database.
- It matches how the steps run on AWS: each one is a Lambda with
  `step(event) -> event`, and steps hand each other **file paths**, not data.
  Step Functions caps the data passed between steps at 256 KB, and the
  Thera-SAbDab file alone is 636 KB.

```mermaid
flowchart LR
    A([Fetch<br/>Thera-SAbDab]):::primary --> B([Normalize<br/>antibodies]):::primary
    B --> C([Fetch CT.gov<br/>per antibody]):::primary
    C --> D([Normalize<br/>trials]):::primary
    D --> E([Load]):::neutral
    E --> F[(Postgres)]:::database

    classDef primary fill:#1e40af,stroke:#bfdbfe,color:#fff
    classDef neutral fill:#374151,stroke:#d1d5db,color:#fff
    classDef database fill:#0f766e,stroke:#99f6e4,color:#fff
    linkStyle default stroke:#333333,stroke-width:2px
```

The order matters: CT.gov is searched once per antibody, so it can only run once
we know which antibodies we track.

Every run writes to its own folder:

```
data/raw/<run_id>/
    therasabdab.csv      raw download
    antibodies.json      normalized antibodies + chains
    ctgov/<inn>.json     raw CT.gov response, one per antibody
    trials.json          normalized, deduplicated trials
```

---

## Part 1 — Thera-SAbDab (antibodies and chains)

**Input:** one CSV, one row per therapeutic, 24 columns, ~1,133 rows.
**Output:** `antibodies.json`, 139 antibodies with 357 chains (as of list PL132).

### 1. Read every cell as raw text

`pd.read_csv(path, dtype=str, keep_default_na=False)`

**What:** every value stays exactly as written in the file.
**Why:** by default pandas silently converts `na`, `None` and blanks into `NaN`
and guesses number types. We want to see the source's own words first and
decide ourselves what counts as missing (step 3).

### 2. Find the stage column by prefix

**What:** the clinical stage column is found by looking for a name starting
with `Highest_Clin_Trial`, not by its full name.
**Why:** the full name contains a date (`Highest_Clin_Trial (Feb '25)`) that
changes with each data release. Matching the exact name would break the
pipeline the next time the source updates. If required columns are missing
entirely, normalize stops with an error listing what it found. A loud failure
is better than silently loading garbage.

### 3. Treat several words as "missing"

**What:** `na`, `None`, `TBC`, `Unknown`, `ND`, `n/a` and blank all become `None`.
**Why:** the source uses different words for "nothing here": `na` for "doesn't
apply" (like a second sequence on a non-bispecific), `TBC` for "to be confirmed",
`None` in the structure columns. For our purposes they all mean "no value", and
one consistent `None` keeps every later check simple.

### 4. Keep only our five targets

**What:** each row's `Target` is matched against five buckets by gene symbol:

| Bucket | Gene symbols matched | Antibodies |
|---|---|---|
| PD-1 | `PDCD1` | 55 |
| PD-L1 | `CD274` | 38 |
| HER2 | `ERBB2` | 20 |
| CD20 | `MS4A1` | 19 |
| IL-23 | `IL23A`, `IL12B` | 9 |

**How the matching works:** the `Target` field packs two kinds of separators.
`;` separates the arms of a bispecific, and `/` separates different names for
the same protein. So `PDCD1/CD279/PD1;ERBB2/CD340/HER2` is split into arms, each
arm into names, and an antibody joins a bucket if *any* name matches. Text in
brackets is ignored, so `ERBB2/CD340/HER2 (Domain II)` still counts as HER2.

**Why gene symbols:** they are the standard, unambiguous IDs. Free-text names
like "PD1" vs "PD-1" vs "CD279" vary; `PDCD1` is always written the same way.

**Why IL12B is in the IL-23 bucket:** some IL-23 drugs, such as ustekinumab, bind
the p40 subunit that IL-23 shares with IL-12, and the source files those under
`IL12B`. Leaving it out would drop well-known IL-23 pathway drugs.

### 5. One antibody can belong to two targets

**What:** each antibody gets a `targets` list (e.g. `["PD-1", "HER2"]`) and a
single `target`, the first match, for the existing column.
**Why:** bispecifics bind two targets on purpose. A PD-1 × HER2 bispecific
belongs in both landscape views; forcing one target would hide it from the other.
This is why the `antibody.targets` column was added.

**Why targets are not tied to a specific chain:** the source isn't consistent
about arm order (`CD3E;MS4A1` and `MS4A1;CD3E` both appear), so we can't safely
say "chain 2 binds HER2".

### 6. Drop veterinary entries

**What:** rows with `(Canine)` or `(Feline)` in the target, or a format starting
with "Canine" or "Feline", are skipped.
**Why:** this is a human therapeutics landscape. A dog PD-1 antibody isn't a
competitor to Keytruda.

### 7. Normalize the clinical stage

**What:** the source's stage text becomes one standard value plus a sortable rank:

| Source says | `clinical_stage` | `stage_rank` |
|---|---|---|
| Preclinical | preclinical | 0 |
| Phase-I | phase_1 | 1 |
| Phase-I/II, Phase-I/Phase-II, Phase-II | phase_2 | 2 |
| Phase-II/III, Phase-III | phase_3 | 3 |
| Preregistration | filed | 4 |
| Approved | approved | 5 |
| TBC, Unknown | unknown | none |

A `(w)` or `(withdrawn)` suffix is stripped and recorded separately as `withdrawn = true`.

**Why:**
- The same stage is spelled several ways (`Phase-I/II` and `Phase-I/Phase-II`;
  `(w)` and `(withdrawn)`). Without merging them, the landscape would show
  duplicate columns.
- Combined phases like Phase I/II count as the higher phase, because that's the
  furthest the drug has demonstrably reached.
- `stage_rank` lets the dashboard sort columns correctly. Alphabetical order
  would put "approved" first and "phase_1" after "filed".
- The original text is kept in `stage_raw` for display and debugging.
- Any stage value not in the table is logged as a warning, so a new term in a
  future release gets noticed instead of silently becoming "unknown".

### 8. Normalize development status

**What:** `Active` → `active`, `Discontinued` → `discontinued`, anything else
(`TBC`, `NFD`, `ND` …) → `unknown`. The original is kept in `status_raw`.
**Why:** only active vs discontinued matters for the landscape. The source's
other codes are rare (`ND` and `Approved` appear once each in that column) and
not clearly defined, so we don't guess what they mean.

### 9. Extract the chains

**What:** up to four chains per antibody:

| Column | Chain |
|---|---|
| `HeavySequence` | heavy, index 1 |
| `LightSequence` | light, index 1 |
| `HeavySequence(ifbispec)` | heavy, index 2 |
| `LightSequence(ifbispec)` | light, index 2 |

**Why `chain_index`:** a regular antibody has one kind of heavy chain and one
kind of light chain. A bispecific has two different arms, so it can have two
different heavy and two different light chains. `(antibody, H/L, index)`
identifies each one uniquely. This is the multi-chain part of the project.

**Cleaning applied to each sequence:**
- Whitespace removed and letters uppercased.
- Must be at least 50 letters long and contain only amino acid letters.
  Otherwise the chain is skipped.

**Why the 50-letter minimum:** the sequences are **variable domains only**
(median 120 amino acids), but a few rows hold a 2-character placeholder instead
of a real sequence. Running liability analysis on those would produce nonsense.
The real run skipped none of the 139 antibodies, so this is a safety net.

### 10. Names and lookup keys

- `display_name` keeps the source's capitalization (`Pembrolizumab`) for the UI.
- `inn_name` is lowercased (`pembrolizumab`). It is the unique key in the
  database and the search term sent to CT.gov.
- `aliases` (code names like `MK-3475`, brand names like `Keytruda`) and
  `companies` are split on `;` into lists.

**Why keep aliases:** many trials list the drug by its development code rather
than its INN. Aliases are the starting point for better matching across sources
(see Known limitations).

**Duplicates:** if the same INN appears twice, the first row is kept and a
warning is logged, since `inn_name` must be unique in the database.

### 11. Source link

**What:** each antibody gets
`.../therasabdab/search/?therapeutic=<Name>`, which opens the source's own
search result for that drug.
**Why:** every signal must link to its source (the database enforces this).
A reviewer should be able to click through and check the claim.

### 12. Content hash (the change fingerprint)

**What:** a sha256 hash of the fields that matter: stage, status, targets,
format and chain sequences.
**Why:** on the next run, comparing two hashes tells us instantly whether
anything meaningful changed, without comparing field by field. Fields that
change without meaning anything for the landscape (company lists, aliases) are
left out on purpose, so they don't trigger false signals.

---

## Part 2 — ClinicalTrials.gov (trials)

**Input:** one CT.gov API v2 response per tracked antibody.
**Output:** `trials.json`, deduplicated trials, each linked to its antibodies.

### 1. How trials are fetched

| Choice | Value | Why |
|---|---|---|
| Search field | `query.intr=<inn_name>` | find trials that use the drug as an intervention |
| Sort | `LastUpdatePostDate:desc` | newest activity first, which is what monitoring cares about |
| Cap | 50 trials per antibody | pembrolizumab alone has ~3,000 trials; 50 keeps a full run to a few minutes |
| Fields | 7 fields via `fields=` | much smaller responses; we only read these |
| Delay | 0.3 s between requests | be polite to a free public API |
| Retries | up to 5, with backoff on 429 / 5xx | survive temporary rate limits and outages |

If one antibody still fails after retries, it's recorded in `ctgov_failed` and
the run continues. A single bad request shouldn't throw away the other 138
antibodies' data; the run can be marked `partial` instead of `failed`.

The run also records CT.gov's `dataTimestamp` (from `/api/v2/version`). CT.gov
refreshes once per weekday, so a future run can skip CT.gov entirely when
nothing has been refreshed since last time.

### 2. Extract the fields

| Our field | Comes from | Example |
|---|---|---|
| `nct_id` | `identificationModule.nctId` | NCT07090499 |
| `title` | `identificationModule.briefTitle` | |
| `sponsor` | `sponsorCollaboratorsModule.leadSponsor.name` | Pfizer |
| `status` | `statusModule.overallStatus` | RECRUITING |
| `phase` | `designModule.phases`, joined with `\|` | PHASE1\|PHASE2 |
| `enrollment` | `designModule.enrollmentInfo.count` | 540 |
| `last_update_date` | `statusModule.lastUpdatePostDateStruct.date` | 2026-10-13 |
| `source_url` | built from the NCT ID | clinicaltrials.gov/study/NCT07090499 |

**Why `|` for phases:** a trial can span two phases (`["PHASE1","PHASE2"]`).
Joining them into one string fits a single column and stays readable.
**Missing sections** become `None` rather than an error. Not every study has
every module filled in. A study with no NCT ID at all is skipped.
**Status values** are kept exactly as CT.gov writes them (`RECRUITING`,
`ACTIVE_NOT_RECRUITING` …), so signals match what users see on CT.gov.

### 3. Merge duplicate trials

**What:** trials are keyed by NCT ID. When the same trial shows up under several
antibodies, it becomes **one** trial with an `inn_names` list of every antibody
that matched it.
**Why:** combination trials are very common in oncology (e.g. a new drug tested
together with pembrolizumab). Storing the trial once and linking it to several
antibodies is what the `antibody_trial` table is for, and it prevents one real
status change from firing duplicate signals.

### 4. Trial content hash

**What:** hash of title, sponsor, status, phase and enrollment.
**Why `last_update_date` is excluded:** it changes on every edit to the record,
including edits we don't care about (a new site address, a contact change).
Including it would make almost every trial look "changed" on every run.

---

## Known limitations (and what production would do)

| Limitation | Effect | Production approach |
|---|---|---|
| CT.gov searched by INN only | trials listing only a code name (MK-3475) are missed | also search aliases, with rules to avoid ambiguous short codes |
| `query.intr` matches combination arms | a trial can be "about" another drug that only uses ours as a partner | classify arm roles, or weight by how the drug appears |
| 50-trial cap per antibody | older but still active trials can be left out | full pagination with incremental fetch by update date |
| Target bucket from gene symbol only | new spellings in future releases may be missed | ontology mapping (e.g. HGNC IDs) |
| Variable domains only, regex-based CDR3 | approximate CDR boundaries | numbering tools such as ANARCI with IMGT/Chothia schemes |
| Thera-SAbDab is a periodic list | stage changes appear only when a new list is published | add regulatory and press-release sources for faster signals |