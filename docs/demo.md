# Demo recording

`docs/demo.gif` is a placeholder frame. Replace it with a roughly 20-second GIF:

1. In a terminal, run `proofjson extract` on `examples/invoice/invoice.pdf` with `--model mock` (about 4 seconds). Show the summary: 20 fields, `notes` below confidence.
2. Open `out.json` and land on `_evidence` for `total` (quote, page 2, verified) (about 4 seconds).
3. Run `proofjson review out.json`. Click `notes`. The page text highlights `Notes: Thank you for your demo order.` (about 6 seconds).
4. Change the note, save the edit, approve the remaining fields, and save `out.reviewed.json` (about 6 seconds).

Record the terminal with [vhs](https://github.com/charmbracelet/vhs) and the review page with a screen capture. All of the sample documents are generated. Do not substitute a real invoice.
