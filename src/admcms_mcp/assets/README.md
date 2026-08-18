# assets

`icon.*` -- het logo dat clients (Claude, Cursor, VS Code) naast de connector
tonen. Wordt uitgeserveerd op `GET /icon.png` en aangekondigd via `icons` in
`serverInfo` bij `initialize`, conform de MCP-spec.

Herkomst van het huidige bestand: `https://www.cmsnl.com/apple-touch-icon.png`.

## Vervangen

Zet je bestand hier neer als `icon.png`, `icon.webp`, `icon.jpg` of `icon.svg`
en push. Geen code-wijziging nodig.

- Het formaat wordt uit de bytes gelezen, niet uit de extensie, en de juiste
  `Content-Type` wordt meegestuurd. Converteren is dus niet nodig -- maar geef
  het bestand wel de extensie die bij het formaat hoort.
- Bij meerdere bestanden wint de eerste uit deze lijst:
  `icon.png`, `icon.webp`, `icon.jpg`, `icon.jpeg`, `icon.svg`.
- Vierkant, minimaal 180x180 (256x256 is prettiger op scherpe schermen).
- Transparante achtergrond werkt in zowel light- als dark-mode.
- Houd het onder ~150 KB: het bestand gaat ook als data-URI mee voor clients die
  geen externe URL ophalen. Daarboven vervalt die variant.
