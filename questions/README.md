# Question format

One JSON file per question. Files are served in name order, so prefix with a number (`018-...json`).

```json
{
  "id": "018-short-slug",
  "topic": "Раздел 1 · Lakeflow Jobs",
  "question": "Text of the question",
  "options": ["option A", "option B", "option C", "option D"],
  "correct": "B",
  "explanation": "Why B is right, quoting docs.databricks.com",
  "sources": ["https://docs.databricks.com/..."]
}
```

`python bot.py check` validates every file (also runs automatically on push).
