-- 0051: an index on equity.filing (event_id) (issue #577, job 2B-16e; #573's verdict, "What the view now costs").
-- 0050's tie of a call to its quarter's first release looks up equity.filing by event_id for every row and every call, and the
-- table has only (entity_id, period_end): on dev that was one sequential scan of about 3,800 rows per lookup (27,258 of 35,966
-- buffer hits for one company's rows). One index, nothing else: no view, column, grant, policy or data change.

create index if not exists filing_event_id_idx on equity.filing (event_id);
