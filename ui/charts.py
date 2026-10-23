"""Single smooth accent-coloured line chart (Altair, bundled with Streamlit)."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd

from ui.theme import TOKENS
from utils.formatting import month_label


def trend_frame(totals: dict[str, Decimal]) -> pd.DataFrame:
    return pd.DataFrame({"month": [month_label(m) for m in totals], "order": list(range(len(totals))),
                         "amount": [float(v) for v in totals.values()]})


def trend_chart(totals: dict[str, Decimal], title: str = "Monthly bill totals", height: int = 260):
    import altair as alt

    df = trend_frame(totals)
    sort = list(df["month"])
    base = alt.Chart(df).encode(
        x=alt.X("month:N", sort=sort, title=None, axis=alt.Axis(labelColor=TOKENS["text_secondary"], labelAngle=0,
                                                                 domain=False, ticks=False, grid=False)),
        y=alt.Y("amount:Q", title=None, axis=alt.Axis(labelColor=TOKENS["text_secondary"], grid=False, domain=False,
                                                       ticks=False, format="$,.0f")),
        tooltip=[alt.Tooltip("month:N", title="Month"), alt.Tooltip("amount:Q", title=title, format="$,.2f")])
    line = base.mark_line(interpolate="monotone", color=TOKENS["accent"], strokeWidth=3,
                          point=alt.OverlayMarkDef(color=TOKENS["accent"], size=60, filled=True))
    return (line.properties(height=height, title=alt.TitleParams(title, color=TOKENS["text_secondary"],
                                                                 anchor="start", fontSize=13, fontWeight=600))
            .configure(background="transparent").configure_view(strokeWidth=0))
