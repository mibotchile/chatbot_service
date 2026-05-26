# Knowledge schema — Cobranza (Fase 1)

> El engine carga JSONs de conocimiento por tenant desde
> `tenants/{slug}/knowledge/*.json` (ver `core/tenant_loader.py`). En Fase 0
> NO hay KB; el loader degrada a vacio. Este documento describe el schema
> objetivo para Fase 1.

## Archivos previstos

### `faq.json`
Preguntas frecuentes de cobranza (como pago, donde pago, que pasa si no pago).
```json
{
  "faqs": [
    {
      "category": "canales_de_pago",
      "questions": ["donde pago", "como pago"],
      "knowledge": { "canales": "...", "horarios": "..." }
    }
  ]
}
```

### `payment_channels.json` (nuevo en cobranza)
Medios de pago disponibles por tenant (links, transferencia, agentes, app).
```json
{
  "channels": [
    { "id": "link", "label": "Pago online", "url": "https://...", "available": true },
    { "id": "transfer", "label": "Transferencia", "account": "...", "available": true }
  ]
}
```

### `discount_rules.json` (nuevo en cobranza, regulatorio)
Reglas de descuento/quita por pronto pago. Lo que el agente PUEDE ofrecer.
```json
{
  "rules": [
    { "min_days_overdue": 90, "max_discount_pct": 20, "requires_approval": false }
  ]
}
```

### `account.schema.json` (forma del detalle de deuda)
No es contenido del tenant sino el contrato del dato que devuelve la fuente de deuda.
```json
{
  "account_id": "string",
  "debtor_name": "string",
  "total_debt": "number",
  "capital": "number",
  "interest": "number",
  "late_fees": "number",
  "days_overdue": "integer",
  "tramo": "string",
  "currency": "string"
}
```

## NOTA de seguridad (Fase 2)
El detalle de deuda es PII financiero. La fuente de deuda real NO debe vivir en
estos JSONs estaticos — debe consultarse via API read-only con verificacion de
identidad previa y audit trail. Estos JSONs solo guardan configuracion no sensible
(canales, reglas de descuento, FAQ).
