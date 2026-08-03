# KIT research-seminar deck

The authoritative scientific content is
`battery-early-warning-research-seminar.md`. The current deliverable is:

`battery-early-warning-research-seminar-kit-b-vs-dcore-development-oof.pptx`

Build it from the repository root with an isolated `uv` environment:

```bash
uv run --isolated scripts/build_research_seminar_pptx.py
```

The script uses inline PEP 723 dependencies (`python-pptx` and Pillow), so it
does not add presentation tooling to the project's runtime dependencies. It
fails if the Markdown no longer has exactly 21 slides and 21 speaker-note
blocks. Each note block is copied into its slide's PowerPoint Notes pane.

The build also writes same-layout PNGs to
`rendered-kit-b-vs-dcore-development-oof/`. They are deterministic visual-QA
previews generated from the same geometry and content as the editable PPTX;
they are not a LibreOffice/PowerPoint rasterization. The checked environment
does not include LibreOffice, so a final presentation-machine smoke test is
still recommended before a live seminar.

Every slide is labeled:

`DEVELOPMENT OOF · B vs D_CORE/EIS · NO PROTECTED TEST · NO SELECTION`

The B-only PPTX and archived 22-slide PPTX predate the current paired run and must not
be used for this seminar.
