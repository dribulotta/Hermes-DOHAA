# Controlled tool recovery simulator

This development-only simulator exercises a boundary the current DOHAA
controller does not execute: an external effect that may happen before its
acknowledgment reaches the caller. It uses synthetic inventory in SQLite and
does not call an LLM, shell, network service or real actuator.

`ToolStore` supports inventory queries, reservations and full releases.
The host seeds inventory and grants each task a maximum total active
reservation for a product. Grants are immutable. Reservations require the
observed inventory version, available stock and sufficient remaining task
quota. A release must match the original task, product and entire reservation
quantity. It cannot increase stock twice.

The host assigns stable operation, task and step identities. `Operation`
binds those identities to the exact payload. Repeating an identity with the
same payload returns its original receipt, including an original rejection.
Changing the payload or assigning a different operation ID to the same
task/step raises `Conflict`. New step IDs must represent new host-approved
steps; these IDs and grants must not be selected by an untrusted model.

`IntentJournal` stores intentions and acknowledgments in a separate database
from the tool's effects and receipts. It admits at most one pending operation
per task. `Executor.recover(task_id)` looks up the tool's receipt before any
retry. If lookup fails, the intent stays pending and no effect is attempted.
A missing receipt permits a retry of the exact same operation only because
this particular tool commits its effect and receipt atomically. Once a receipt
is recorded and checked against the intention, the journal can mark the
operation complete. A completed rejection remains a rejection, not task success.

```python
from pathlib import Path
from tools.controlled_tools import Executor, IntentJournal, Operation, ToolStore

# Use two new files in a dedicated development directory.
tool = ToolStore(Path("tool.sqlite3"))
journal = IntentJournal(Path("intents.sqlite3"))
tool.seed("synthetic-widget", 10)
tool.authorize("task-a", "synthetic-widget", 7)
executor = Executor(journal, tool)
operation = Operation("host-op-1", "task-a", "reserve-1", "reserve",
                      "synthetic-widget", 4, expected_version=0)
receipt = executor.execute(operation)
assert receipt["status"] == "applied"
assert executor.execute(operation) == receipt
assert tool.inventory("synthetic-widget")["available"] == 6
```

The regression suite terminates actual child processes before intent creation,
after durable intent, inside the effect transaction before receipt insertion,
after the effect commits but before acknowledgment, after acknowledgment and
after completion. Reopened stores must recover one reservation, never two.
Other tests cover concurrent duplicates and conflicting reservations, stale
versions, quota, ownership, conflicting IDs, failed lookup and malformed receipts.
SQLite transactions use `synchronous=FULL`; these tests cover process crashes,
not physical power failure, disk corruption or hostile database writers.

This is a trusted local simulator, not a general exactly-once guarantee for
external APIs. Receipt binding detects mismatched payloads; it is not receipt
authentication. Database paths, initial data and grants remain host-controlled.
The fixed `document-stream-proposal/1.0` contract and installed native Hermes
remain unchanged. An eventual LLM/tool bridge needs its own fixed operation
contract, isolation, host authorization and comparison protocol before use.

Related: issue #82. Agent-generated implementation; maintainer review required.
Rollback: omit this standalone module and its tests/docs; no runtime integration
or data migration is enabled by this change.
