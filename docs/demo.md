# Demo recording

`docs/demo.gif` is a 20-second recording of the generated sample invoice:

1. In a terminal, run `proofjson extract` on `examples/invoice/invoice.pdf` with `--model mock` (about 4 seconds). Show the summary: 20 fields, `notes` below confidence.
2. Open `out.json` and land on `_evidence` for `total` (quote, page 2, verified) (about 4 seconds).
3. Run `proofjson review out.json`. Click `notes`. The page text highlights `Notes: Thank you for your demo order.` (about 6 seconds).
4. Change the note, save the edit, approve the remaining fields, and save `out.reviewed.json` (about 6 seconds).

All of the sample documents are generated. Do not substitute a real invoice.

## Regenerate

`scripts/record_demo.py` rebuilds the GIF. It runs the commands above with `--model mock` (no API key), screenshots the review page, and writes `docs/demo.gif`. ffmpeg and Google Chrome are required.

```bash
uv run --with playwright --with pillow python scripts/record_demo.py
```
