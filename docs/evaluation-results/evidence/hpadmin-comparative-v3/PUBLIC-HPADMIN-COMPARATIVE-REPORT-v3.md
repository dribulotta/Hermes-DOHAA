# HPADMIN V3: comparación local controlada

**Resultado según criterios preregistrados:** `NOT_PASSED`

Los agregados y valores p fueron recalculados desde las etiquetas `pass` de la matriz caso por caso; no se utilizó el resumen privado. Como las propuestas no se publican, el bundle verifica compromisos y recomputa estadísticas, pero no re-puntúa independientemente cada propuesta.

| Condición | Aciertos estrictos | Tasa | Fallos runtime | Llamadas atribuidas medias | Reintentos |
|---|---:|---:|---:|---:|---:|
| Direct | 15/40 | 37.50% | 0 | 1 | 0 |
| Razonamiento solicitado (high; no atestado) | 15/40 | 37.50% | 0 | 1 | 0 |
| Self-reflection | 15/40 | 37.50% | 0 | 2 | 0 |
| DOHAA | 16/40 | 40.00% | 0 | 1.62 | 1 |

## Comparación primaria

DOHAA vs Direct: **1 victorias, 0 derrotas y 39 empates** (ambos pasan=15, ambos fallan=24); p exacta bilateral=1.
La p exacta es una regla de decisión prerregistrada sobre esta suite fija; no implica muestreo poblacional, independencia entre casos ni generalización fuera de la suite.

## Comparaciones secundarias

- `dohaa_vs_native_reasoning_requested`: W=1, L=0, empates=39 (ambos pasan=15, ambos fallan=24), p=1.
- `dohaa_vs_self_reflection`: W=1, L=0, empates=39 (ambos pasan=15, ambos fallan=24), p=1.
- `native_reasoning_requested_vs_direct`: W=0, L=0, empates=40 (ambos pasan=15, ambos fallan=25), p=1.
- `self_reflection_vs_direct`: W=1, L=1, empates=38 (ambos pasan=14, ambos fallan=24), p=1.

## Cómputo físico y atribuido

Solicitudes físicas globales: **146**. Llamadas lógicas atribuidas: **225**. La inferencia inicial compartida reduce cómputo físico; no se cuenta como ventaja semántica.

## Balance del orden de ramas

Cada dominio conserva balance 4/4 tanto para el orden de llamadas iniciales como para el orden de refinamiento Self-reflection/DOHAA. El detalle recomputable está en `initial_call_order_balance`, `branch_order_balance` y la matriz.
La identidad del input visible se recalculó contra el contrato canónico de la suite para las cuatro condiciones; violaciones=0.

## Frontera del oráculo

El output previo al scoring quedó comprometido con SHA-256 `2d9ca1dbc69af47cf9a211ac7ec44dc79e3ff0ec296495ba613526573ec00a49`. Después se cargó el oráculo para puntuar. `oracle_feedback_events=0`; el controlador no accedió al resultado esperado ni a `result_equals`.
Se auditaron 26 eventos de feedback DOHAA: todos provinieron de la allowlist exacta de compuertas de producción; eventos prohibidos=0. `semantic_assertions` fue exclusivo del scorer y nunca estuvo disponible al controlador.

## Invalidez de V2

V2 queda como evidencia de desarrollo invalidada para su afirmación primaria: seis de sus ocho victorias DOHAA dependieron de feedback exclusivo del oráculo. No participa en V3.

## Razonamiento nativo

`high` fue solicitado y configurado, pero no existe telemetría independiente que pruebe su activación interna: se rotula **solicitado, no atestado**.
De igual modo, `none` en Direct, Self-reflection y DOHAA describe la configuración solicitada; este bundle no atesta independientemente el estado interno de razonamiento de ninguna de las cuatro condiciones.
El identificador del artefacto del modelo también es declarado por el operador y no está independientemente atestado por este bundle.
El endpoint local fue observado por el operador; el renderer no lo atesta independientemente. Durante la ejecución se exigieron `redirects_allowed=false` y `proxy_environment_used=false`; la atestación pública complementaria verifica la observación local sin convertirla en una prueba externa independiente.

## Hardware

`UNKNOWN`: no se aportó evidencia sanitizada. Este bundle no demuestra por sí solo operación sobre hardware accesible.

## Alcance de la afirmación

DOHAA se evalúa aquí como sistema completo: LLM + validador determinístico de políticas desplegable + reparación acotada. Un PASS solo respalda esta comparación preregistrada sobre la suite sintética, el modelo declarado y esta ejecución. Los presupuestos de cómputo pueden diferir y se informan.

No atribuye la mejora a razonamiento abstracto, no compara DOHAA contra un motor de políticas determinístico independiente y no demuestra superioridad universal, pagos autónomos seguros, rendimiento productivo ni reemplazo de autoridad humana.
Los casos son instancias nuevas de cinco esquemas administrativos congelados; no son cinco diseños de tarea completamente nuevos.

## Integridad

- Suite canónica SHA-256: `d7d97c6ac19b096b7c51fccfb67a8b269a7b5605615043ce3ac394399bd51953`
- Resultado privado SHA-256: `6606cc5f343dc8acddcc87952dc400eeebbd243dfe39ea6f7b8d279925bb8bd4`
- Compromiso: `4a10b38c-9907-4494-a44b-1bde5dfb38dd`
- Compromiso de código (SHA-256 del manifiesto del paquete): `d1175e2b4a411363489617b5039bf34320c2ca25a2e4fc4cef4b99eb131a45d0`. La cadena es verificable; no constituye por sí sola atestación independiente de ejecución.
- El verificador enlaza las fuentes públicas ejecutables con el manifiesto original. La autenticidad posterior a la ejecución requiere publicar externamente, por un canal inmutable, el digest del bundle o del manifiesto.
- La matriz pública omite propuestas, prompts, contratos, inputs y respuestas esperadas.
