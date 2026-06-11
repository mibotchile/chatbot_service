# Definiciones de negocio — Naomi Ramos (Prestamype) — 2026-06-10

Fuente: respuesta por correo de Naomi Ramos (Analista de Gestión y Procesos de Cobranza,
nramos@prestamype.com) al pliego de consultas enviado por Ricardo el 2026-06-08.
Email id 11366 (cuenta onbotgo). Adjunto: `FERIADOS_NACIONALES_2026.pdf`.

Estas definiciones cierran la lógica de negocio del change
`prestamype-cobranza-flujos-escenarios`.

---

## 1. Cálculo de conceptos moratorios

### Penalidades (sobre saldo capital inicial)
- **1ª semana de atraso**: `0.008% × saldo_capital_inicial`, resultado **redondeado**
  (ejemplo: S/5.66 → S/5.70).
- **2ª semana de atraso**: `0.016% × saldo_capital_inicial`.

### Intereses compensatorios
```
interes_compensatorio = amortizacion_cuota
                      × tasa_interes_mensual / 30
                      × dias_transcurridos
```
`dias_transcurridos` = días de diferencia entre **fecha de vencimiento** y
**fecha de pago estimada**.

---

## 2. Escenarios

| Pregunta | Definición confirmada |
|---|---|
| Caso real "cliente al día" | **P04069** (provisto por Naomi). |
| Caso real "cuota próxima a vencer" | **No disponible.** Vencimientos de junio llegan solo hasta 12/06; la regla dispara a 5 días, así que hoy no hay registros que cumplan. |
| Umbral "próxima a vencer" | Cuota que vence en los **próximos 5 días**. CONFIRMADO. |
| Flujo "próxima a vencer" | **Mismo flujo y opciones** que "cliente al día" (consultar cronograma / subir comprobante / hablar con asesor) + mostrar **fecha y monto**. |

---

## 3. Créditos e identificación

- **Multi-crédito**: un cliente puede tener **hasta 2 créditos en paralelo**.
  Cuando hay 2, mostrar **detalle diferenciado por cada crédito** con estos
  diferenciadores:
  - valor de cuota
  - cuenta bancaria
  - CCI
  - inversionista
  - plazo
  - fecha de vencimiento
  - inicio del préstamo (primera cuota)

- **Identificación**: por **DNI** o por **ID crédito** (el ID crédito funciona
  como código general).
  ⚠️ **Matiz vs. propuesta original**: la info de deuda se muestra **únicamente a
  los involucrados del préstamo** (titular y garante), no "sin distinción de rol
  abierto". Los involucrados se identifican con su DNI o el ID crédito.

---

## 4. N.° de cuota (comprobante)

- Es un **correlativo** (1, 2, 3…).
- Coincide con la columna **"Nro Cuotas"** del archivo de pagos de Prestamype.
- Debe coincidir exactamente con lo que el cliente ve en su cronograma.

---

## 5. Reglas operativas

### Compromiso de pago (CONFIRMADO)
- Fecha comprometida **> 2 días** → derivar a asesor.
- Fecha comprometida **≤ 2 días** → registrar compromiso.

### Horario de atención
- **Lunes a Viernes, 9:00 a.m. – 6:30 p.m.**
- **Refrigerio: 1:00 p.m. – 2:00 p.m.** (asesores NO disponibles).
- Fuera de ese horario → flujo "fuera de horario".

### Feriados nacionales 2026 (pago que cae en feriado/domingo)
| Fecha | Día | Feriado |
|---|---|---|
| 2026-01-01 | Jueves | Año Nuevo |
| 2026-04-02 | Jueves | Jueves Santo |
| 2026-04-03 | Viernes | Viernes Santo |
| 2026-05-01 | Viernes | Día del Trabajo |
| 2026-06-07 | Domingo | Batalla de Arica y Día de la Bandera |
| 2026-06-29 | Lunes | San Pedro y San Pablo |
| 2026-07-23 | Jueves | Día de la Fuerza Aérea del Perú |
| 2026-07-28 | Martes | Fiestas Patrias |
| 2026-07-29 | Miércoles | Fiestas Patrias |
| 2026-08-06 | Jueves | Batalla de Junín |
| 2026-08-30 | Domingo | Santa Rosa de Lima |
| 2026-10-08 | Jueves | Combate de Angamos |
| 2026-11-01 | Domingo | Día de Todos los Santos |
| 2026-12-08 | Martes | Inmaculada Concepción |
| 2026-12-09 | Miércoles | Batalla de Ayacucho |
| 2026-12-25 | Viernes | Navidad |
