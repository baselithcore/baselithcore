# Validation reports

Evidence artifacts for the TRL 5 validation program. Each campaign run files
a dated directory here:

```text
<YYYY-MM-DD>-<campaign>/   # load | soak | chaos | eval | ops | pilot
├── report.md              # per the template in the campaign runbook
├── report.json            # machine-readable results (when emitted)
└── raw/                   # harness raw output
```

Reports are **immutable once merged** — corrections are new reports.

- Acceptance criteria: `mkdocs-site/docs/validation/vv-plan.md`
- How to run campaigns: `mkdocs-site/docs/validation/campaigns.md`
- Assessment status: `mkdocs-site/docs/validation/trl5-evidence-matrix.md`
