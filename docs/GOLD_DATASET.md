# Реальный gold-датасет

`gold_dataset.py` содержит отдельный hash-only контур ручной разметки. Он не
делает машинные кандидаты подтверждёнными примерами: импорт оставляет каждую
задачу в состоянии `pending`, а переход в gold возможен только после явного
решения аннотатора.

## Поток разметки

```text
corpus_annotation.jsonl
        │ import-candidates
        ▼
queue.jsonl (pending, 47 522 кандидата; поверхностей нет)
        │ annotate --decision accepted/rejected
        ▼
реальные подтверждённые gold-записи
```

Для полноты recall можно добавить вручную найденный span командой `add`; такая
задача получает `machine_generated: false`. Все записи содержат только
`document_id`, координаты, label/layout, SHA-256 исходника и provenance/licence.
Абсолютные пути, текст, surface и примечания (только их хеш) не сохраняются.

```bash
venv/bin/python app/gold_dataset.py import-candidates candidates.jsonl gold/queue.jsonl \
  --manifest gold/manifest.json --license public_pending_verification
venv/bin/python app/gold_dataset.py queue gold/queue.jsonl --label INN --limit 20
venv/bin/python app/gold_dataset.py annotate gold/queue.jsonl TASK_ID \
  --decision accepted --annotator reviewer-1 --layout text
venv/bin/python app/gold_dataset.py coverage gold/queue.jsonl
venv/bin/python app/gold_dataset.py metrics gold/queue.jsonl predictions.jsonl \
  --output gold/metrics.json
```

`coverage` считает только `accepted` + `annotator.status=confirmed` +
`provenance.real=true`. Цель — минимум 200 подтверждённых реальных примеров
для каждого из 90 типов. Пока ручная разметка не проведена, отчёт обязан
показать дефицит; синтетические 200 примеров и 47 522 кандидата не заменяют
этот минимум.

Для локального запуска Luna pre-review используется отдельный статус
`model_reviewed`. Он содержит только provenance, confidence и reason, не меняет
`human_confirmed` и не засчитывается в gold:

```bash
venv/bin/python app/gold_dataset.py luna-pre-review \
  .cache/legal-corpus/gold/queue.jsonl \
  .cache/legal-corpus/gold/luna_queue.jsonl \
  --manifest .cache/legal-corpus/gold/luna_manifest.json \
  --deficit .cache/legal-corpus/gold/deficit.json
venv/bin/python app/gold_dataset.py deficit \
  .cache/legal-corpus/gold/queue.jsonl \
  --output .cache/legal-corpus/gold/deficit.json
```

Очередь ограничивается 200 реальными кандидатами на тип. В локальном срезе
доступны кандидаты только для 12 из 90 типов; 3 типа имеют не менее 200
кандидатов. Для остальных типов отчёт указывает дефицит и необходимость
дополнительных лицензированных реальных источников либо ручных spans. Ни один
дефицит не заполняется синтетическими примерами.

`metrics` считает exact-span precision/recall/F1 по каждому entity и по
`text`/`table`/`ocr`, а также micro/macro, false positives, false negatives и
confusion. Выход ошибок тоже hash-only. Split-ы манифеста строятся
детерминированно от `text_sha256`, поэтому один документ не может попасть в
несколько split-ов.
