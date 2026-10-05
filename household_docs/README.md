# Household Docs — Home Assistant app

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

> **Documents are plain files.** Every note, checklist and sheet is an ordinary file in Home Assistant's `/share`
> folder, so anyone who can reach `/share` (Samba, the File editor, other apps, a backup that includes Share)
> can read and change it. Passwords and card numbers belong in Household Vault.

Notes, checklists and sheets for everyone in a Home Assistant home, kept in folders and shared with chosen people or
with everyone. Everyone opens it from the Home Assistant sidebar and is recognised by their Home Assistant
account — there is no separate login. Each person's documents are in a folder of their own under `/share`, so
they also open over Samba, in the File editor or in any text editor. Nothing leaves your Home Assistant. See
the Documentation tab (DOCS.md) for the full guide.

- **Notes and checklists** — plain-text (`.txt`) and Markdown (`.md`) notes with a preview, Find & Replace,
  tappable links and `[[links]]` between documents (with *Linked from*), and checklists (Markdown task lists)
  with autosave, who ticked what, hide ticked, untick all and swipe to tick on phones.
- **Organise** — tags and colours, up to 8 pinned documents on your home page (a checklist's progress, a sheet's
  chosen cell), ⚡ Quick note (also `N`, or a dashboard button), print and Download as PDF.
- **Sheets** — spreadsheets with formulas, tabs, charts and conditional colours, saved as `.xlsx` (or `.csv`)
  that open in Excel and LibreOffice; Excel files from elsewhere open read only when they hold things the app
  can't keep.
- **Folders** — your own **My docs**, as deep as you like, plus Favourites, Recent and Trash.
- **Sharing** — any document or folder with chosen people or **Everyone**, as *Can view*, *Can edit* or
  *Manager*; folder shares reach everything inside, including files added later. A phone notification tells
  people when something is shared with them.
- **Safe saving** — two people editing at once get *Keep mine / Use theirs / Compare*; changes made outside the
  app are noticed; earlier versions are kept under **History**.
- **Search** — everything you can open, by name, pattern or regular expression, by the text inside, and by date,
  size, type, person, sharing, tag and colour; saved searches.
- **Stay up to date** — 🕑 Activity, Follow for phone notifications (batched, with quiet hours), Home Assistant
  sensors for a checklist, a sheet's cell or a folder, and a storage report with duplicates.
- **Bring things in** — PDF text search, 📷 Scan with the phone's camera (cropped, straightened, one PDF), Google
  Keep and `.zip` imports.
- **Optional AI** — off until an admin sets it up: read text in scans, summaries, checklists and sheets from text,
  questions about a folder; every action says which provider gets what before sending.
- **Connect and tidy** — send a document to a chat in Household Chat (a card with *Open in Docs*, never the
  content) and turn a checklist into a Household Todo list; templates; filing rules for files that arrive
  (rename, move, Undo) and clean-up rules; a Kids' space with child accounts; dots for what changed since you last
  looked.
- **Admin** — who has access, shared `/share` folders, where the documents folder is (with a folder browser, and
  a checked move to a new location: you copy, the app checks the copy and switches), App settings, and backup
  and restore.
