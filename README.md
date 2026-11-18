# Startup Screening Desk

A working prototype of one workflow: take a batch of early-stage start-ups, gather what is known about each from the public web and from documents the company sent, compare them, and put the decisions in front of a person with one click each:

1. Is it worth a first meeting with stakeholders? (Yes / No)
2. How much could be co-invested? (accept one of the options)

For a portfolio growth team it also answers a third question from the same evidence: what does this company need from us, under which workstream, and what kind of organisation could help?

Everything runs from one **Start** button.

## Run it

```bash
python run_server.py
```

Then open http://127.0.0.1:8770. It needs Python 3.10 or later and the packages in `requirements.txt`.

- **Live runs** need `ANTHROPIC_API_KEY`. On Windows a key saved at User level is found automatically.
- **With no key or no internet**, Start plays back the stored recording of the last live run of the sample batch, and says so on screen.
- The server does not reload code. After a change to a `.py` file, stop it and start it again. Until then the screen shows a banner saying it is running an older version, and Start is switched off.

## What Start does

For each start-up, at the same time:

| Step | Who does it | What happens |
|---|---|---|
| 1. Read the documents | reader model | PDF, Word, PowerPoint, Excel, text and CSV files are read with citations on, so every fact comes back tied to a quoted passage and a page |
| 2. Search the public web | reader model + web search | It is given the name, website and one-line description only. Nothing from the uploaded documents is sent to the search. A finding that comes back with no source gets one more pass in the same conversation, then is dropped |
| 3. Build the profile | judge model | Headcount, products, stage, round, co-investor, customers, share of wallet, competitors, market estimates, each naming the evidence it rests on |
| 4. Map what it needs from the team | reader model | Needs sorted under the team's workstreams (go-to-market access, Singapore landing plan, R&D and centre of excellence, talent, investor engagement), each citing its evidence, with the kind of organisation that could help. Kinds only: no person or contact is ever named |
| 5. Check sources | code | Every figure and name is looked up in the quoted passage it cites. A figure that is not there is flagged and never used. A need that cites nothing is dropped |
| 6. Scheme checks, score, options | code | Startup SG Equity eligibility, a weighted score, and the co-investment amount from the published schedule |

Then once for the batch:

| Step | Who does it | What happens |
|---|---|---|
| 7. Compare and brief | judge model | A headline, what separates the start-ups, a summary and first-meeting questions for each. Wording only: it cannot change a score or an amount |
| 8. Up to three plans | code | Follow the app's read; meet first and decide capital later; back the top-ranked only. Plans that make the same decisions are shown once. Accept one, then change any single decision |

A person makes every decision. Each one is logged with who made it, when, and whether it overrode the app. Nothing is sent to anyone: the invitation, the reply and the request for documents are drafts to copy.

## Three reads, not two

For the first meeting the app says **Yes**, **No**, or **No call yet**.

- **No** needs a reason the evidence supports: a hard scheme requirement fails, or the score is under the threshold on enough evidence.
- **No call yet** is what the app says when evidence coverage is under 50%, or when the criteria it could rate carry under 60% of the score's weight. Little public evidence is not the same as a weak company. The app then drafts a request for exactly the items it could not find.
- A criterion the evidence says nothing about is **not rated** and is left out of the score. It is not counted as weak.
- A call made on partial evidence says what it rests on ("This read rests on 9 of the 17 items the desk looks for and 5 of the 7 criteria").

The thresholds are in `decision_rules` in `data/policy.json`.

## What is real and what is invented

- **Invented:** the three sample companies (Halcyra Materials, Orvelle Diagnostics, Brevanta AI), their people, investors, customers, figures and documents. Each sample file says so in its footer, and since 18 Nov 2026 so does every result and every memo of the sample batch, recorded or live (before that, only a toast said so when the batch was loaded, and a replay never showed it).
- **Checked against real companies on 18 Nov 2026:** ACRA's open register on data.gov.sg (October 2026 data) holds no entity named Halcyra, Orvelle, Brevanta, Breventa or Vantrel; its one Quenby is a business named QUENBY whose registration was cancelled, not a fund. The same searches return HALO, ORIENTAL and BYTEDANCE entities, so they work. A web search finds no company of these names either. Two real companies sit close: Green Li-ion, a Singapore start-up that turns spent lithium-ion batteries back into battery-grade cathode, is what Halcyra is described as (the sample run's own search returned it and reported none of its facts), and Brevian, a Sunnyvale start-up with a no-code platform for building AI agents for support teams, is near Brevanta AI in name and product. Check again whenever a sample name changes: a test fails until this line names every sample company.
- **Real:** the web research (live searches, real market reports and real competitors, each with its link), the model calls, and the co-investment rules.
- Type any real company into a card and the same workflow runs on it. Give it the website and one line as well as the name: a name alone gives the search little to go on, and the screen says so before Start.

## Where the amounts come from

The model never sets an amount. Code applies the published Startup SG Equity co-investment schedule, held in `data/policy.json` with the page each number was read from and the date it was read (16 Nov 2026):

- Deep tech: cap S$12 million for each start-up. Government to co-investor 2:1 up to the first S$1 million (first institutional round; otherwise 1:1), 1:1 up to S$4 million, 1:2 up to S$8 million, 1:3 up to S$12 million.
- Sources: [Startup SG Equity eligibility](https://www.startupsg.gov.sg/programmes/4895/startup-sg-equity/eligibility) (parameters as of 27 Feb 2026) and [SEEDS Capital, our co-investment model](https://www.seedscapital.sg/about-us/our-co-investment-model/) (updated 2 July 2026).
- The four score criteria that carry most of the weight are the four attributes [SEEDS Capital's FAQ](https://www.seedscapital.sg/about-us/faqs/) says it assesses.
- The two pages disagree on one point: the SEEDS Capital page still lists a general tech row, and the Startup SG page describes the scheme for deep tech only. The app shows the row, never recommends capital on it, and says why.
- An amount is only ever suggested against a round that is open now. A round the evidence says has closed is history: its terms are flagged, shown with a warning, and kept out of the sizing.

The readings this app makes of those pages are listed under Basis on every result, and in `assumptions` in `data/policy.json`. They are mine and should be confirmed with the team. The five workstream headings are in `workstreams` in the same file, with where they were taken from.

## How it guards against invented facts

1. **Citations or nothing.** A line the reader writes with no quoted passage behind it is not used. For an uncited line from a document, code first looks for a passage that carries its figures, then the reader is asked for the passage, then the judge model. For an uncited web finding, the reader is asked once more, in the same conversation, for the page behind it. What is still unsourced is thrown away and listed in the audit trail.
2. **Evidence ids.** The judge model sees only numbered evidence and must name the ids behind every fact. A fact naming no valid id is dropped. The same holds for every need.
3. **Source check in code.** A figure not found in its cited passage is flagged, shown with a warning and kept out of the scheme checks and the sizing.
4. **Rules in code.** Eligibility, score, share of wallet, amounts and the three reads are computed by code from `data/policy.json`.
5. **Briefing check.** Any figure in the written briefing that is not in the verified data is flagged above the result.

## Models and cost

Two roles, set in Run settings on the screen or by environment variable:

| Role | Default | Does |
|---|---|---|
| Judge (`SSD_MODEL`) | `claude-opus-5-5` | builds the profile, rates it, writes the briefing |
| Reader (`SSD_READER_MODEL`) | `claude-haiku-5-5` | reads documents, searches the web, maps the needs |

Measured on 16 Nov 2026 (estimates from token counts):

| Batch | Judge | Reader | Time | Cost |
|---|---|---|---|---|
| Sample, three start-ups with documents | Opus 5.5 | Haiku 5.5 | 73 s | about US$0.74 |
| Sample, three start-ups with documents | Haiku 5.5 | Haiku 5.5 | 75 s | about US$0.17 |
| One real company, name only, no documents | Haiku 5.5 | Haiku 5.5 | 79 s | about US$0.06 |

The two sample rows were measured before the needs step and the second web pass were added, so a run today costs somewhat more. The third row was measured after.

Server-side refusal fallback is switched on for the models that support it (`SSD_FALLBACKS=off` turns it off).

## Limits

- A first-pass screen, not due diligence. The amounts are ceilings under a published schedule, not valuations.
- The score comes from 1 to 5 ratings by a model, so two runs can differ by a few points. The weights are on screen and can be changed; the ratings and their reasons are shown.
- On a real company with no documents the app has the public web only. Expect gaps, and expect "No call yet" when it finds too little.
- "Who could help" is a kind of organisation read from the evidence. The app has no contact list and matches nobody to anybody.
- A web citation quotes at most 150 characters, so a figure can be cited without appearing in the quote. Those are marked "cited", with the link to open.
- Uploaded documents are sent to the model provider to be read. On real company material this should run on a provider the organisation has approved; the model sits behind one file (`ssd/llm.py`).
- Amounts are in Singapore dollars; other currencies are not converted.
- A growth-stage round gets no suggested amount, because no ratio is published for direct investment.
- It is not connected to any organisation's systems and has never seen real deal data.

## Files

```
run_server.py                 start the app
ssd/pipeline.py               the workflow behind Start
ssd/rules.py                  schedule, scheme checks, score, the three reads, options, plans (code, no AI)
ssd/checks.py                 source checks (code, no AI)
ssd/docs.py                   document reading and citable passages
ssd/llm.py                    the one place that talks to the model
ssd/schemas.py                the structured answers the models must give
ssd/server.py                 HTTP API
prompts/                      the five prompts, as plain text files
data/policy.json              the published rules, the decision thresholds and the workstreams, with sources
data/samples/                 the invented sample batch
data/recorded/                the last live run of the sample batch, for playback
web/                          the screen (plain HTML, CSS and JavaScript)
tests/test_offline.py         43 tests that run with no network and no key
scripts/make_samples.py       rebuild the invented sample documents
scripts/try_live.py           run a batch from the command line
scripts/refresh_recording.py  re-run the code-only steps on the stored recording after a rule change (no model call)
scripts/add_support.py        add the needs step to the stored recording from its stored evidence
```

## Tests

```bash
python -m pytest -q
```

The tests include the buttons driven through the HTTP layer (Yes, No, Accept, Accept this plan, the weights panel, the memo, document upload).

Claude Code wrote this code on 16 Nov 2026 from a brief; the brief, the testing and the reading of its mistakes are the author's.
