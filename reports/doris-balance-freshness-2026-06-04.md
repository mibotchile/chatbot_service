# Doris Balance Freshness Analysis — PrestamYpe
**Date:** 2026-06-04  
**Scope:** `batch_asignacion_review_bronze` + `batch_pagos_v2_bronze`  
**DB:** `project_QUIdI0iwQY0l3pJwRKLB` @ `10.110.0.15:9030`  
**Status:** READ-ONLY investigation — no code changes

---

## 1. Date/Batch-Identifying Columns

### `batch_asignacion_review_bronze`
| Column | Type | Role |
|--------|------|------|
| `creado_el` | `datetime` | Row insert timestamp (when Doris ingested the file) |
| `archivo` | `varchar(100)` | **Source filename** — the batch identifier (e.g. `Asignacion_20052026_F.csv`) |

No `fecha_carga`, `fecha_asignacion`, `fecha_proceso`, `batch_id`, `periodo`, `created_at`, or `load_date` columns exist. The only batch discriminators are `archivo` (file name, encodes date) and `creado_el` (ingest timestamp).

### `batch_pagos_v2_bronze`
| Column | Type | Role |
|--------|------|------|
| `creado_el` | `datetime` | Row insert timestamp |
| `archivo` | `varchar(100)` | Source filename (e.g. `pagos_27042026.csv`) |
| `fecha_de_pago_del_cliente` | `date` | **Actual payment date** (when client paid) |
| `fecha_de_pago_esperada_original` | `date` | **Due date** per installment schedule |
| `fecha_amortizacion` | `date` | Amortization date |

Payment date range in live data: `2022-03-23 → 2026-12-31`.

---

## 2. Multi-Batch Structure — Live Evidence

The assignment table currently holds **3 distinct batches** (all loaded May 2026):

| `archivo` | `creado_el` (first row) | Rows | Unique credits |
|-----------|------------------------|------|----------------|
| `Asignacion_19052026.csv` | 2026-05-19 11:46:17 | 905 | 513 |
| `Asignacion_20052026.csv` | 2026-05-20 08:52:21 | 905 | 513 |
| `Asignacion_20052026_F.csv` | 2026-05-20 13:08:37 | 853 | 480 |
| **TOTAL** | | **2,663** | — |

**10 credits appear in all 3 batches** (up to 18 rows each per assignment table).

Sample credit `P04197` across batches:
```
archivo=Asignacion_19052026.csv   creado_el=2026-05-19 11:46:18  capital=108450.0  dias_mora=44
archivo=Asignacion_20052026.csv   creado_el=2026-05-20 08:52:21  capital=108450.0  dias_mora=44
archivo=Asignacion_20052026_F.csv creado_el=2026-05-20 13:08:38  capital=108450.0  dias_mora=48
```

Note: `dias_mora` changed from 44 → 48 between the 19-May batch and the final 20-May batch. **The latest batch (`_F`) is the authoritative state.**

---

## 3. The Staleness Bug — CONFIRMED

### What the current query does

Generated SQL (from `_build_sql` in `doris_debt_source.py`):

```sql
SELECT
  a.id_credito AS account_id,
  a.id_credito AS loan_number,
  a.nombre_completo AS borrower_name,
  a.dni_ruc AS dni,
  ...  -- all non-aggregated debt columns → GROUP BY
  MAX(p.cuota_esperada_actualizada) AS cuota_esperada,
  MAX(p.cuota_esperada_actualizada) AS next_installment_amount,
  MAX(p.saldo_por_cancelar) AS saldo_por_cancelar,
  MAX(p.saldo_por_cancelar) AS balance
FROM project_QUIdI0iwQY0l3pJwRKLB.batch_asignacion_review_bronze a
JOIN project_QUIdI0iwQY0l3pJwRKLB.batch_pagos_v2_bronze p
  ON p.codigo_contrato = a.id_credito
WHERE a.dni_ruc = %s
GROUP BY a.id_credito, a.nombre_completo, a.dni_ruc, ...
ORDER BY days_overdue DESC
```

**Two compounding problems:**

#### Problem A — Assignment table: no batch filter
The JOIN fans out over ALL assignment batches. For a credit in 3 batches (e.g. `P04197` = 18 assignment rows), each assignment row joins to all 18 pagos rows → **324 combined rows** before GROUP BY. The GROUP BY collapses them, but the non-aggregated debt columns (like `dias_mora`) are resolved non-deterministically — Doris may return any batch's value, not necessarily the latest.

Verified for `P04197`:
```
Current query result:
  MAX(saldo)=108450.0   MIN(saldo)=6915.8   pagos_rows=324   MAX(cuota)=None
```

#### Problem B — `saldo_por_cancelar` semantics: it's a per-installment REMAINING balance, not a scalar "total owed"

The pagos table has **one row per installment** (18 installments for `P04197`). `saldo_por_cancelar` is the **cumulative remaining balance as of that installment's due date** — it decreases monotonically as installments are paid:

```
fecha_esp=2025-10-02  saldo=108450.00  (oldest — full balance)
fecha_esp=2025-11-02  saldo=103240.05
fecha_esp=2025-12-02  saldo=97942.57
...
fecha_esp=2026-06-02  saldo=64235.54   ← current (today = 2026-06-04)
...
fecha_esp=2027-03-02  saldo=6915.80   (last installment)
```

`MAX(saldo_por_cancelar)` = **108,450.00** — the balance as of the FIRST installment (oldest, already paid in Oct 2025). This is the **total original loan amount**, not the current debt.

**The chatbot is telling debtors their current balance is the original loan amount**, not what they actually owe today.

The correct "current balance" for `P04197` is approximately **81,510.15** (the saldo of the most recently overdue installment with no `fecha_de_pago_del_cliente`).

---

## 4. The `pagos` Table — Payment Date Analysis

`batch_pagos_v2_bronze` has **only one archivo** (`pagos_27042026.csv`, loaded 2026-04-27), meaning it was loaded ~3 weeks before the assignment files. The pagos file has:

- `fecha_de_pago_del_cliente`: actual payment date (NULL when not yet paid)
- `fecha_de_pago_esperada_original`: installment due date
- `status`: `SEGUIMIENTO` (due/overdue), `TODAVIA NO VENCE` (not yet due)

**"Payments >= file load date" filter feasibility:**  
The pagos file load date is `2026-04-27` (`MIN(creado_el)` in pagos table). Filtering `fecha_de_pago_del_cliente >= MAX(a.creado_el)` (assignment load date) is technically possible but would exclude valid earlier payment history and is not the right fix. The installment schedule is baked into the file — it already reflects the state at load time.

---

## 5. Correct "Current Balance" Logic

### Assignment table fix — pick latest batch per credit

Add a CTE or subquery to select only the **latest assignment row per credit** (by `MAX(creado_el)` or `MAX(archivo)` which encodes date):

```sql
WITH latest_assignment AS (
  SELECT *
  FROM batch_asignacion_review_bronze
  WHERE (id_credito, creado_el) IN (
    SELECT id_credito, MAX(creado_el)
    FROM batch_asignacion_review_bronze
    GROUP BY id_credito
  )
)
```

Or equivalently filter on the latest `archivo`:

```sql
WHERE a.archivo = (
  SELECT archivo FROM batch_asignacion_review_bronze
  ORDER BY creado_el DESC LIMIT 1
)
```

(The global latest archivo is `Asignacion_20052026_F.csv`.)

### Pagos balance fix — use MIN not MAX (or filter to the "current" installment)

`saldo_por_cancelar` is monotonically **decreasing** per installment date. The current outstanding balance is the saldo of the **earliest unpaid installment** (first row where `fecha_de_pago_del_cliente IS NULL`):

**Option A — Correct aggregation for total remaining balance:**  
Use `MIN(p.saldo_por_cancelar)` instead of `MAX`. `MIN` picks the last installment's saldo, which equals the **remaining balance on the last installment** — but this is the balance if ALL remaining installments were paid, not the current overdue amount. This is still semantically "total loan remaining."

**Option B — Current overdue balance (first unpaid installment):**  
```sql
-- saldo at the first installment that hasn't been paid yet
SELECT MIN(p.saldo_por_cancelar) FILTER (WHERE p.fecha_de_pago_del_cliente IS NULL
                                           AND p.status = 'SEGUIMIENTO')
```
This gives the current overdue amount only.

**Option C — Most likely correct for collections context:**  
The saldo of the installment with the earliest `fecha_de_pago_esperada_original` where `fecha_de_pago_del_cliente IS NULL` — i.e., the first unpaid installment's running balance:
```sql
SELECT p.saldo_por_cancelar
FROM batch_pagos_v2_bronze p
WHERE p.codigo_contrato = :id_credito
  AND p.fecha_de_pago_del_cliente IS NULL
ORDER BY p.fecha_de_pago_esperada_original ASC
LIMIT 1
```
For `P04197` this returns **81,510.15** (March 2026 installment not paid), which is the actual current overdue running balance.

---

## 6. Recommended Fix Summary

| Issue | Current | Fix | Column |
|-------|---------|-----|--------|
| Assignment staleness | All batches joined | Filter to `MAX(creado_el)` per `id_credito` | `a.creado_el` |
| Balance semantics | `MAX(saldo_por_cancelar)` = original amount | First unpaid installment's `saldo_por_cancelar` (ORDER BY `fecha_de_pago_esperada_original` ASC, filter `fecha_de_pago_del_cliente IS NULL`) | `p.saldo_por_cancelar`, `p.fecha_de_pago_del_cliente`, `p.fecha_de_pago_esperada_original` |
| `cuota_esperada` | `MAX(cuota_esperada_actualizada)` = may be wrong installment | Same filter as balance (current installment) | `p.cuota_esperada_actualizada` |

Implementation path: `doris_debt_source.py::_build_sql` needs to support a "latest batch only" JOIN strategy and a "first unpaid installment" aggregation instead of naked `MAX`. This is a **schema-level design change** — the current flat `agg: MAX` in `tenant.config.json` is insufficient to express this logic. A `strategy: "first_unpaid"` or similar key in `doris_schema` would be needed, or the SQL is hardened for PrestamYpe specifically.

---

## Appendix: Exact Column Names for Fix

**`batch_asignacion_review_bronze`** — batch discriminators:
- `archivo` — file name (encodes date, e.g. `Asignacion_20052026_F.csv`)
- `creado_el` — ingest timestamp

**`batch_pagos_v2_bronze`** — balance + payment columns:
- `saldo_por_cancelar` — cumulative remaining balance (decreases per installment)
- `fecha_de_pago_del_cliente` — actual payment date (NULL = not yet paid)
- `fecha_de_pago_esperada_original` — installment due date
- `cuota_esperada_actualizada` — current installment amount
- `status` — `SEGUIMIENTO` (due/overdue) | `TODAVIA NO VENCE` (future)
- `creado_el` / `archivo` — pagos batch discriminators
