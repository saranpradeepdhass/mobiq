"""HTML fragment builders (pure, testable) plus thin Streamlit render helpers.
Every dynamic value passes through esc(); fragments are rendered with unsafe_allow_html."""
from __future__ import annotations

from utils.calculations import LineSummary, ShareView, Status
from utils.formatting import date_label, esc, format_money, mask_phone, month_label

_GLYPH = {Status.PAID: "✓", Status.DUE: "○", Status.OVERDUE: "!"}


def status_pill(status: Status) -> str:
    return (f'<span class="mq-pill {status.name.lower()}" role="status">'
            f'<span aria-hidden="true">{_GLYPH[status]}</span>{esc(status.value)}</span>')


def metric_card(label: str, value: str, sub: str = "", focus: bool = False) -> str:
    return (f'<div class="mq-card{" focus" if focus else ""}"><div class="mq-label">{esc(label)}</div>'
            f'<div class="mq-value">{esc(value)}</div><div class="mq-meta-2">{esc(sub)}</div></div>')


def grid(cards: list[str], cols: int = 4) -> str:
    return f'<div class="mq-grid cols-{cols}">{"".join(cards)}</div>'


def empty_state(title: str, text: str) -> str:
    return f'<div class="mq-empty"><h4>{esc(title)}</h4><div>{esc(text)}</div></div>'


def notice(kind: str, text: str) -> str:
    label = {"error": "Error", "success": "Done", "info": "Note", "warning": "Check"}.get(kind, "Note")
    role = "alert" if kind in ("error", "warning") else "status"
    return f'<div class="mq-notice" role="{role}"><b>{label}:</b>{esc(text)}</div>'


def table(headers: list[str], rows: list[list[str]], numeric: set[int] | None = None,
          raw_cols: set[int] | None = None, secondary: set[int] | None = None) -> str:
    """rows: cell strings. Columns in raw_cols are trusted HTML (built by this module); others are escaped."""
    numeric, raw_cols, secondary = numeric or set(), raw_cols or set(), secondary or set()
    num_attr = ' class="num"'
    head = "".join(f'<th scope="col"{num_attr if i in numeric else ""}>{esc(h)}</th>' for i, h in enumerate(headers))
    body = ""
    for r in rows:
        cells = ""
        for i, c in enumerate(r):
            cls = " ".join(x for x, on in (("num", i in numeric), ("sec", i in secondary)) if on)
            attr = f' class="{cls}"' if cls else ""
            cells += f"<td{attr}>{c if i in raw_cols else esc(c)}</td>"
        body += f"<tr>{cells}</tr>"
    return (f'<div class="mq-table-wrap" tabindex="0" role="region" aria-label="Table, scrollable">'
            f'<table class="mq-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')


def payment_table(views: list[ShareView], currency: str, show_user: bool = True) -> str:
    headers = (["User"] if show_user else []) + ["Line", "Number", "Month", "Amount due", "Paid", "Outstanding", "Due date", "Status"]
    rows = []
    for v in views:
        row = ([v.user.name if v.user else "—"] if show_user else []) + [
            v.line.line_label if v.line else "—", mask_phone(v.line.mobile_number) if v.line else "••••",
            month_label(v.month), format_money(v.amount_due, currency), format_money(v.paid, currency),
            format_money(v.outstanding, currency), date_label(v.due_date), status_pill(v.status)]
        rows.append(row)
    off = 1 if show_user else 0
    return table(headers, rows, numeric={off + 3, off + 4, off + 5}, raw_cols={off + 7}, secondary={off + 1, off + 2})


def payments_made_table(views: list[ShareView], currency: str) -> str:
    """One row per month and line for shares marked paid: amount and the date it was marked paid."""
    rows = []
    for v in sorted(views, key=lambda x: (x.month, x.line.line_label if x.line else ""), reverse=True):
        rows.append([month_label(v.month), v.line.line_label if v.line else "—", format_money(v.paid, currency),
                     date_label(v.share.paid_date), status_pill(v.status)])
    return table(["Month", "Line", "Amount paid", "Paid on", "Status"], rows, numeric={2}, raw_cols={4}, secondary={1})


def history_table(views: list[ShareView], currency: str, show_line: bool = True) -> str:
    headers = ["Month"] + (["Line"] if show_line else []) + ["Amount due", "Amount paid", "Outstanding", "Status"]
    rows = []
    for v in sorted(views, key=lambda x: (x.month, x.line.line_label if x.line else "")):
        rows.append([month_label(v.month)] + ([v.line.line_label if v.line else "—"] if show_line else []) + [
            format_money(v.amount_due, currency), format_money(v.paid, currency),
            format_money(v.outstanding, currency), status_pill(v.status)])
    n = 1 + (1 if show_line else 0)
    return table(headers, rows, numeric={n, n + 1, n + 2}, raw_cols={n + 3})


def line_card(s: LineSummary, currency: str) -> str:
    kv = lambda k, v: f'<div class="mq-kv"><span>{esc(k)}</span><span>{esc(v)}</span></div>'
    prev = ""
    if s.previous:
        items = "".join(kv(f"{month_label(v.month)} unpaid", format_money(v.outstanding, currency)) for v in s.previous[:4])
        prev = f'<div class="mq-meta" style="margin-top:.75rem">Previous unpaid bills</div>{items}'
    return (f'<div class="mq-card"><div class="mq-line-head"><div><div class="mq-line-name">{esc(s.line.line_label)}</div>'
            f'<div class="mq-meta">{esc(mask_phone(s.line.mobile_number))}'
            f'{"" if s.line.active else " · inactive"}</div></div>{status_pill(s.status)}</div>'
            f'<div style="height:.75rem"></div>'
            f'{kv("Current bill", format_money(s.current_due, currency))}'
            f'{kv("Paid", format_money(s.current_paid, currency))}'
            f'{kv("Outstanding", format_money(s.current_outstanding, currency))}'
            f'{prev}</div>')


# ---- Streamlit render helpers ------------------------------------------------
def render(fragment: str) -> None:
    import streamlit as st

    st.markdown(fragment, unsafe_allow_html=True)


def show(kind: str, text: str) -> None:
    render(notice(kind, text))


def section(title: str) -> None:
    render(f'<div class="mq-section">{esc(title)}</div>')
