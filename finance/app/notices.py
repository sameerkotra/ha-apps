"""Messages a page shows after a form sends the person back to it ("Typed text didn't match", "Give the car
a name", ...).

The address carries only a short code (`?error=no_match`), never the sentence itself: a link someone else
made could otherwise put any text they liked on the app's own page, looking like the app said it. An
unknown code shows nothing.
"""

MESSAGES = {
    # Deleting (routes/deletion.py)
    "no_match": "Typed text didn't match — nothing was deleted.",
    "name_no_match": "Typed name didn't match — nothing was deleted.",
    "processing": "It is still being read — wait for it to finish (or fail), then try again.",
    "account_processing": "A statement for this account is still being processed — wait for it to finish, then try again.",
    "user_processing": "Some of this person's uploads are still being read — wait for them to finish, then try again.",
    "live_rows": "This account still has live statements or transactions — restore or delete them first.",
    # Restoring (routes/deletion.py, utilities, tolls)
    "account_deleted": "Its account is deleted — restore the account first.",
    # Admin → Storage (routes/admin_storage.py)
    "import_not_confirmed": "Import not confirmed.",
    "import_failed": "The backup couldn't be restored — nothing was changed. The app's log says why.",
    "import_busy": "A PDF is being read right now — try the restore again when it has finished.",
    "import_not_sqlite": "That file isn't a valid SQLite database.",
    "import_not_finance": "That doesn't look like a Finance Dashboard database (some of its tables are missing).",
    "import_damaged": "That database failed its integrity check — it may be damaged.",
    # Tolls (routes/tolls.py)
    "car_name_missing": "Give the car a name.",
    "car_name_taken": "You already have a car with that name.",
    "group_name_missing": "Give the group a name.",
    "group_name_taken": "You already have a group with that name.",
    "tag_invalid": "Give the tag a name and a start time that is not after its end time.",
    "tag_gone": "That tag no longer exists.",
    "too_long": "That is longer than the field allows.",
    # Upload → Statements without its script (routes/upload.py)
    "upload_sent": "Your files were sent — see how each one went in Upload history below.",
    "upload_some_failed": "Your files were sent, but some couldn't be read — see Upload history below. "
                          "A QFX file whose account couldn't be worked out needs the page with its script.",
    # Setup → Accounts → an account's upload settings (routes/accounts.py)
    "layout_cleared": "The saved CSV layout was cleared.",
    # Setup → Categories (routes/categories.py)
    "cat_added": "Category added.",
    "cat_renamed": "Category renamed — its transactions and rules follow.",
    "cat_merged": "Merged — its transactions and rules moved.",
    "cat_moved": "Moved to the other list. Its transactions keep their category.",
    "cat_hidden": "Hidden from the menus. Transactions that have it keep it, and its rules still work.",
    "cat_shown": "Back in the menus.",
    "cat_colour": "Colour saved.",
    "cat_deleted": "Category deleted.",
    "rule_saved": "Rule saved.",
    "rule_copied": "Copied into your rules — change it below. Yours is tried before the built-in one.",
    "suggestion_made": "Rule made.",
    "suggestion_dismissed": "Suggestion dismissed — it won't be offered again.",
    "income_sorted": "Your income was sorted.",
}


def text(code: str | None) -> str:
    """The sentence for a code from the address, or "" for anything else."""
    return MESSAGES.get((code or "").strip(), "")


def wants_page(request) -> bool:
    """A plain browser form post (not htmx, not a script asking for JSON): answer with a page."""
    accept = request.headers.get("accept", "")
    return "text/html" in accept and request.headers.get("hx-request") != "true"


def form_error(request, templates, user, acting_qs: str, message: str, back: str, status_code: int = 400):
    """What a form that couldn't be saved answers: for a browser, a page saying why with a link back
    (the browser keeps what was typed when going back); for a script, {"error": message} as before."""
    if wants_page(request):
        return templates.TemplateResponse(request, "form_error.html", {
            "user": user, "acting_qs": acting_qs, "message": message, "back": back,
        }, status_code=status_code)
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": message}, status_code=status_code)
