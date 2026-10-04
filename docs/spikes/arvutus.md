# Spikes S6–S8: the arvutus sandbox, the editor, Mouser (2026-10-03)

For docs/ARVUTUS-PLAN.md.

## S6: the sandbox container

`sandbox/Dockerfile` (Python 3.13 slim, numpy 2.3.4, pandas 2.3.3, uid 1001) run as compose runs it:
`network_mode: none`, read-only root, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`,
`pids_limit: 64`, 768 MB, 1 CPU. A run (fresh `python -I kernel.py` with rlimits) takes **about
0.5 s** including numpy; that fits the 800 ms debounce, so no pre-forked process is needed.

| Attempt | Result |
|---|---|
| TCP to 1.1.1.1, DNS | `Network is unreachable`, name resolution fails |
| write outside /tmp | `Read-only file system` |
| the run's environment | PATH, HOME, TMPDIR, LANG and three tuning variables only |
| `/proc/1/environ`, `/proc/1/mem` (the server) | `Permission denied` (the server is non-dumpable) |
| `while True: pass` | killed by RLIMIT_CPU |
| `time.sleep(60)` | stopped by the wall-clock limit |
| 2 GB allocation | `MemoryError` (RLIMIT_AS) |
| fork bomb | stops at the pids limit; the next run works |
| a `setsid` daemon and a file in /tmp | both gone before the next run |

Found and fixed during the spike:

- **The host's `kernel.yama.ptrace_scope` is 0**: a process of the same user could read another's
  memory or open its pipes. The server sets `PR_SET_DUMPABLE 0`, runs one note at a time, and kills
  every process but itself and empties `/tmp` before and after each run.
- **Zombies:** as the container's PID 1 the server inherits every orphan; unreaped they filled the
  pids limit and no further run could start. It now reaps them.
- **A leftover process held the output pipe open**, so `communicate()` waited 120 s and blocked
  every other run. The server waits for the kernel process, then kills the rest, then reads.
- **Killing every process is only safe as PID 1**: outside the container (tests) it would take the
  developer's session with it. The server only does it when its pid is 1.

The attacks are `tests/arvuta/test_arv_sandbox.py` (`pytest -m docker`, `ARVUTUS_SOCKET` set).

## S7: the editor

- **Muya keeps only the first word of a fence's info string**: ```` ```python arvuta peida ````
  came back as ```` ```python ````. So the cell kinds are languages of their own: `arvuta` and
  `arvuta-peida`; plain `python` blocks never run. Muya's language list has no alias hook, so cells
  are not syntax-highlighted.
- **`replaceMarkdown` replaces the whole range between the first and the last changed block**, the
  block being typed in included. `updateMarkdown` (tools/editor/collab.js) matches equal top-level
  blocks as a longest common subsequence and removes and inserts only the others.
- Muya re-pads table cells; the inputs hash is taken over parsed cells, so padding never starts a run.

## S8: Mouser

- Product pages answer scripts with Akamai's "Access to this page has been denied".
- `POST https://api.mouser.com/api/v1/search/partnumber?apiKey=…` answers without a key with
  `{"Errors":[{"Code":"Invalid","PropertyName":"API Key"}]}`.
- **Checked with the user's key (2026-10-04):** `859-LTST-C190KRKT` came back as LITEON "Single
  Colour LEDs Red Clear 631nm", 448,117 in stock, 0,084 € a piece at 10. The field names, the price
  format ("0,084 €") and the currency (EUR for mouser.ee) are as `app/mouser.py` reads them.
- **Links can carry a language:** the user's first order pasted
  `https://www.mouser.ee/en/ProductDetail/859-LTST-C190KRKT`, which the first pattern did not
  take as a Mouser link (the row stayed empty without a warning). Fixed: an optional
  `/<lang>/` before `ProductDetail`, and a subdomain such as `eu.mouser.com`.
- **Before the key was there, these were unchecked:** the field names (`MouserPartNumber`, `PriceBreaks[].Price`,
  `AvailabilityInStock`, `Min`, `Mult`, `ProductDetailUrl`) are the ones Mouser documents and
  `app/mouser.py` reads; the price string's format and the currency for an Estonian account; the
  `qs` match for a link with several results. To do once the user's key is on the Mouser card.
