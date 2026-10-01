"""Server-side sorting and paging for the long history tables (Uploads:
statement and CSV import history; Utilities: upload history).

These tables can be longer than one page, so a client-side sort (static/ui.js)
would only reorder the rows currently shown. Instead the route sorts the whole
list, slices out one page, and the template renders headers and pager links
built by TableView — plain links, so it works without JavaScript and every
state (sort, direction, page, page size) is in the URL.

Each table has its own parameter prefix, so two tables on one page (the
Uploads page has two) page and sort independently:
    <prefix>sort, <prefix>dir (asc|desc), <prefix>page, <prefix>size (10|20|50|all)
"""
from urllib.parse import parse_qsl, urlencode

PAGE_SIZES = (10, 20, 50, "all")
DEFAULT_SIZE = 10


def _positive_int(raw, default: int) -> int:
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


class TableView:
    """One sorted, paged table. `rows` is the current page; the template calls
    sort_url / page_url / size_url for links and reads sort, direction, page,
    pages, size, total, first and last."""

    def __init__(self, request, path: str, prefix: str, rows: list, keys: dict,
                 default_sort: str, default_dir: str = "desc", extra_qs: str = ""):
        self._path = path
        self._prefix = prefix
        self._params = dict(parse_qsl(extra_qs))
        self._params.update(request.query_params)

        get = self._params.get
        sort, direction, size = (get(prefix + n) for n in ("sort", "dir", "size"))
        if sort in keys:
            self.sort, self.direction = sort, direction if direction in ("asc", "desc") else "asc"
        else:
            self.sort, self.direction = default_sort, default_dir
        self.sizes = PAGE_SIZES
        self.size = "all" if size == "all" else int(size) if size in ("20", "50") else DEFAULT_SIZE

        ordered = self._sorted(rows, keys[self.sort])
        self.total = len(ordered)
        if self.size == "all":
            self.pages, self.page, start, end = 1, 1, 0, self.total
        else:
            self.pages = max(1, -(-self.total // self.size))
            self.page = min(_positive_int(get(prefix + "page"), 1), self.pages)
            start, end = (self.page - 1) * self.size, self.page * self.size
        self.rows = ordered[start:end]
        self.first = start + 1 if self.rows else 0
        self.last = start + len(self.rows)

    def _sorted(self, rows: list, key) -> list:
        """Rows whose key is None (no date yet, not parsed yet) always go last,
        in either direction."""
        present = [r for r in rows if key(r) is not None]
        missing = [r for r in rows if key(r) is None]
        present.sort(key=key, reverse=self.direction == "desc")
        return present + missing

    def _url(self, **changes) -> str:
        params = dict(self._params)
        for name, value in changes.items():
            if value is None:
                params.pop(self._prefix + name, None)
            else:
                params[self._prefix + name] = value
        return self._path + ("?" + urlencode(params) if params else "")

    def sort_url(self, key: str) -> str:
        """Header link: sort by `key`; clicking the current column reverses it."""
        flip = "asc" if self.direction == "desc" else "desc"
        return self._url(sort=key, dir=flip if key == self.sort else "asc", page=None)

    def direction_url(self) -> str:
        return self._url(sort=self.sort, dir="asc" if self.direction == "desc" else "desc", page=None)

    def page_url(self, page: int) -> str:
        return self._url(page=page if page > 1 else None)

    def size_url(self, size) -> str:
        return self._url(size=None if size == DEFAULT_SIZE else size, page=None)

    @property
    def show_pager(self) -> bool:
        return self.total > DEFAULT_SIZE or self.size != DEFAULT_SIZE
