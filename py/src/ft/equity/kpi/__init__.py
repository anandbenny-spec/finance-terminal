"""The KPI-table pilot (Plan 05 §1.3, D7; Data Audit 10): finding the pilot document per company and quarter, cutting it
from its bundle, extracting the KPI metrics a page states (through `ft/ai/client.py`), and the post-filters, sign
convention and tile check that Data Audit 10 §8 calls for. Pure logic here; the network and the database are the task's
(`ft/jobs/tasks/equity_kpi_tables.py`). The documents are read and NOT stored (Plan 05 D7, #287)."""
