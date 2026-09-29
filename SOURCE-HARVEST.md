# Personal homepage and rave sources

Registered sources: GeoCities, Angelfire, Tripod, FortuneCity and Rave.ca.

`harvest_personal_and_rave.py` samples each source by year through 2008, saves successful queries, retries failed queries on later runs, and stops a source after two consecutive failed years. JPEG, PNG and GIF candidates are accepted by archived MIME type, including extensionless image URLs. Generic interface assets are filtered. Dates refer to archive capture, not necessarily photograph creation.

```sh
python3 harvest_personal_and_rave.py --merge
# For systems where Python lacks the operating system's certificate trust:
python3 harvest_personal_and_rave.py --transport curl --merge
# A smaller collection, with checkpoints retained for reruns:
python3 harvest_personal_and_rave.py --source Rave.ca --from-year 2004 --to-year 2004 --limit 200 --transport curl --merge
```

Without --merge, only checkpoints and a query report are written. Merge reserves up to 500 sampled entries per source, deduplicates against the existing catalog, preserves unrelated regions, and retains the existing 3,000/source and 6,000/region limits. At capacity, older entries in affected regions may be displaced; a complete pre-merge backup is saved first. Identical harvest input is repeat-safe. Individual catalog files are atomically replaced; run one writer at a time.

Live smoke check on September 29, 2026: all five 2004 queries timed out using the system HTTPS client. No new candidates were returned or merged; the 30,787-entry catalog remains unchanged. The Python client's certificate problem was avoided with curl while keeping certificate verification enabled. Failed requests remain eligible for retry. Source support is implemented, but live collection and replay of new-source images are not yet verified.

Validation: `python3 -m unittest test_source_harvest -v` checks MIME/date/domain filtering, capped merging, repeat safety, failed-query retry and cached empty results.
