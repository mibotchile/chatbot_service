# Migrations

Plain SQL migration files for the chatbot-cobranza database.

## Convention

Files are named `YYYYMMDD_<slug>.sql`.  Each file is self-contained with:
- A PREFLIGHT block (mandatory checklist before running)
- Idempotency guards (`IF EXISTS` / `IF NOT EXISTS` / `CASE` blocks)
- A Rollback / Reverse migration section

## How to run

```bash
psql -h <DB_HOST> -U <DB_USER> -d <DB_NAME> -f migrations/<file>.sql
```

Migrations iterate over all non-system schemas automatically.
Replace `<DB_HOST>`, `<DB_USER>`, `<DB_NAME>` with your environment values.

## Active migrations

| File | Description | Status |
|------|-------------|--------|
| `20260603_refactor_debtor_rename.sql` | Rename sorelia_leads→debtors, lead_*→debtor_* columns, enum remap, drop dead columns | PENDING DEPLOY |
