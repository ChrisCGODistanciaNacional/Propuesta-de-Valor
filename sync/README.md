# Sincronización de `data.json`

La página lee `data.json` (misma carpeta) para mostrar el diagnóstico por asesor. Este folder tiene todo lo que hace falta para regenerarlo; lo corre una tarea programada de Claude (11:00 y 18:00, hora de Lima) con los accesos de Christopher.

## Fuentes

| Archivo de entrada (`sync/inputs/`, no se sube al repo) | Rango que se lee | Spreadsheet |
|---|---|---|
| `feedback.json`  | `'🔰FEEDBACK'!A1:R2000` | Archivo I · `1SJAgbcxpVXpRZ6_Vb4_VW1Pl9BJZMyR8cTH-hmvgdEE` |
| `tecnicas.json`  | `'🏆 TECNICAS'!A1:Z60`  | Archivo I |
| `diasem.json`    | `'🔰DIA/SEM'!A19:IF23`  | Archivo I (matrículas del mes por asesor, su meta y Logro %) |
| `dashboard.json` | `'DASHBOARD'!B5:O90`    | Matriz_CGO_Distancia_2027-I · `1MaiDiBNBicpttRJs2oSAyKvbAD0nI963anN9wfYoeOM` (cuartil de calidad, mapa de calor, focos) |
| `checklist.json` (opcional) | `'✅ CHECKLIST'!A1:AZ80` | Archivo I (validación manual del supervisor) |

Cada archivo es el JSON tal cual lo devuelve el conector de Google Sheets (`{"range": ..., "values": [[...]]}`).

## Pasos

1. Guardar los rangos en `sync/inputs/`.
2. `python3 sync/build_data.py` → escribe `data.json` y `sync/pending.json`.
3. Si `pending.json` trae `casos` o `checklist` pendientes, Claude los escribe en `sync/casos_cache.json` / `sync/checklist_cache.json` (ver formato abajo) y vuelve a correr el paso 2.
4. Commit y push de `data.json` + los cachés.

## Qué calcula `build_data.py`

- Historial por asesor ordenado por fecha (la "última llamada" es la más reciente por fecha, no la última fila).
- Por etapa: `pct` (última), `prev` (anterior), `delta`, `avg` (promedio de todas sus evaluaciones).
- Calidad promedio y cuartil (Q4 ≤ 74 · Q3 75–84 · Q2 85–94 · Q1 ≥ 95) desde la Matriz (o calculado si el asesor no está en ella).
- Matrículas del mes y meta desde 🔰DIA/SEM; avance = matrículas del mes ÷ meta del mes — `AVANCE_MODO = 'mes'` (Logro % real del mes, lo que pidió Christopher); con `'ritmo'` se prorratearía a los días hábiles transcurridos. Cuartil de matrículas: Q1 ≥ 100 % · Q2 80–99 · Q3 70–79 · Q4 < 70. Cuadrante = cruce de ambos (🟢 verde, 🟡 amarillo, 🟠 naranja, 🔴 rojo); nivel gamificado en la página: 🌱 Promesa › ✨ Talento › ⭐ Estrella › 👑 Leyenda.
- Ranking: primero el cuadrante (🟢 › 🟡 › 🟠 › 🔴), dentro de cada uno el promedio entre calidad y avance (el avance cuenta hasta 150 %).
- Checklist: se leen las últimas 3 llamadas evaluadas de cada asesor (`VENTANA_CHECKLIST`); un ítem queda ✅ si la lectura más reciente que lo menciona dice "ok", 🔸 si dice "pendiente", y ◻️ si ninguna de las 3 lo menciona (si estaba ✅ en una llamada más antigua, se marca como retirado).
- Bloque `equipo`: mapa de calor (pauta), foco cualitativo, oportunidad, distribución por cuartiles, listas por cuadrante, ranking completo.
- Nombres: alias explícitos (`ALIASES`) + cruce por parecido (p. ej. "JOSTYN FLORES SOLANO" ↔ "Justyn Flores Solano"); el nombre canónico es el de 🔰FEEDBACK.

## Formato de los cachés

`casos_cache.json` (clave: nombre del asesor):
```json
{"stage": "paso3", "tecId": "tec_08", "tipo": "speech", "porQue": "...", "speech": "...", "base": "<12 hex>"}
```
`checklist_cache.json` (clave: `base` de la LLAMADA evaluada = hash de asesor + fecha + textos):
```json
{"asesor": "Diana Carol Taipe Rojas", "fecha": "06/10/2026", "items": {"paso1_1": {"estado": "ok|pendiente|sin_evidencia", "evidencia": "..." }}}
```
`base` lo entrega `pending.json`; si la evaluación cambia, cambia la `base` y esa llamada vuelve a quedar pendiente. Las llamadas sin texto del supervisor no se piden: quedan sin evidencia.
