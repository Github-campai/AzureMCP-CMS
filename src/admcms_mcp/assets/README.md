# assets

`icon.png` — het logo dat clients (Claude, Cursor, VS Code) naast de connector
tonen. Wordt uitgeserveerd op `GET /icon.png` en aangekondigd via `icons` in
`serverInfo` bij `initialize`, conform de MCP-spec.

Herkomst: `https://www.cmsnl.com/apple-touch-icon.png` — het eigen logo van de
klant, 180x180 PNG met transparante achtergrond.

## Vervangen

Zet een nieuw vierkant PNG op deze plek en push. Geen code-wijziging nodig; de
route en de metadata lezen dit bestand. Richtlijnen:

- vierkant, minimaal 180x180 (256x256 is prettiger voor scherpe schermen)
- PNG met transparante achtergrond werkt in zowel light- als dark-mode
- houd het onder ~100 KB: het bestand wordt ook als data-URI meegestuurd voor
  clients die geen externe URL ophalen
