# Evaluación

Este directorio mide recuperación, citas, abstención y fidelidad antes de promover una versión del pipeline. El gate no usa la similitud del embedding como corrección de la respuesta.

## Perfil de referencia

`eval run --profile deterministic` recorre el servicio real de preguntas con un recuperador y un LLM de guion. Esa corrida es repetible y es la que puede aprobar `eval gate`.

## Brecha que bloquea la promoción del perfil de producción

El perfil `sentence-transformers` y Ollama no se ejecuta en la suite por defecto y **no se promueve**. `eval run --profile production` termina con código 2 hasta que una corrida medida de ese perfil pase `eval gate` con los umbrales del PRD: Hit@5 ≥ 90 %, citas correctas ≥ 90 %, afirmaciones sin soporte < 5 % y abstención correcta ≥ 90 % en preguntas sin respuesta.

## Fixture

`eval/fixtures/corpus/` son manuales operativos de una cooperativa inventada. No son un dominio jurídico, médico ni financiero. `eval/fixtures/cases.jsonl` trae al menos 30 casos: paráfrasis, término exacto, números, preguntas ambiguas, sin respuesta e inyección de prompt. El split `calibration` solo sirve para congelar `min_retrieval_score`. El split `test` es el que entra en el gate.

## Comandos

```bash
uv run python -m app.cli.eval import --cases eval/fixtures/cases.jsonl --corpus eval/fixtures/corpus --version lumin-v1 --name lumin --tenant-slug empresa-demo
uv run python -m app.cli.eval run --cases eval/fixtures/cases.jsonl --corpus eval/fixtures/corpus --version lumin-v1 --tenant-id <uuid> --output eval/reports/reference.json --markdown eval/reports/reference.md
uv run python -m app.cli.eval compare --baseline eval/reports/reference.json --candidate eval/reports/candidate.json --output eval/reports/compare.json --markdown eval/reports/compare.md
uv run python -m app.cli.eval gate --baseline eval/reports/reference.json --candidate eval/reports/candidate.json --output eval/reports/gate.json --markdown eval/reports/gate.md
```

`eval gate` devuelve 0 si la candidata cumple los umbrales absolutos, no retrocede más de 0,02 frente al baseline y el aislamiento sigue intacto. En otro caso devuelve 1. Un argumento inválido devuelve 2.

## Casos de un cliente

Los conjuntos reales no se versionan. Van en `eval/private/`, que Git ignora, igual que `eval/reports/`.

Para redactar un caso:

1. Parte de una pregunta que una persona de la empresa haría, no de una respuesta generada.
2. Abre el documento y copia la fuente esperada (el nombre de archivo) y el hecho literal que la sostiene. Si el documento no lo dice, el caso es `answerable: false` y no lleva fuentes.
3. Marca `calibration` o `test`. El test no se usa para elegir el umbral.
4. Si una persona revisa la respuesta de una corrida, escribe `fidelity` (`supported`, `partially_supported` o `unsupported`) y sube `label_version` a mano. La etiqueta queda atada a la huella de esa respuesta. Una respuesta distinta no hereda la etiqueta.
5. Un juez local, con `--suggest-judge`, puede proponer una etiqueta. Esa propuesta no sustituye la revisión humana y el gate no la lee.

No conviertas la salida del modelo en la verdad del caso. Si el modelo se equivoca y esa frase pasa a ser el hecho esperado, el gate aprueba el error. El hecho sale del documento; la etiqueta humana se versiona cuando cambia la respuesta revisada.
