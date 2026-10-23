"""Centralised design tokens and CSS. One accent hue (electric violet) on a neutral dark palette."""
from __future__ import annotations

TOKENS = {
    "canvas": "#0A0A0F",      # 60%
    "surface": "#161B22",     # 30%
    "border": "rgba(255,255,255,0.05)",
    "accent": "#7C3AED",      # 10% - the only hue
    "text": "#FFFFFF",
    "text_secondary": "#8B949E",
    "text_meta": "#484F58",
}

_FONT = "'Inter','Geist','SF Pro Text',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root {{
  --mq-canvas: {TOKENS['canvas']};
  --mq-surface: {TOKENS['surface']};
  --mq-border: {TOKENS['border']};
  --mq-accent: {TOKENS['accent']};
  --mq-accent-glow: rgba(124,58,237,0.18);
  --mq-text: {TOKENS['text']};
  --mq-text-2: {TOKENS['text_secondary']};
  --mq-meta: {TOKENS['text_meta']};
  --mq-green: #3FB950;   /* Paid */
  --mq-red: #F85149;     /* Due / Overdue */
  --mq-radius: 14px;
}}
html, body, [class*="css"], .stApp {{ font-family: {_FONT}; font-weight: 500; color: var(--mq-text); }}
.stApp {{ background: var(--mq-canvas); }}
header[data-testid="stHeader"] {{ background: transparent; }}
/* Hide Streamlit's own chrome (menu, deploy button, run indicator, footer). The header and toolbar themselves must
   stay: the sidebar expand button lives inside them, and on phones it is the only way to open the navigation. */
[data-testid="stMainMenu"], [data-testid="stAppDeployButton"], [data-testid="stStatusWidget"],
[data-testid="stDecoration"], #MainMenu, footer {{ display: none !important; }}
.block-container {{ padding: 1.5rem 2rem 4rem; max-width: 1280px; }}
h1, h2, h3, h4 {{ font-family: {_FONT}; color: var(--mq-text); letter-spacing: -0.01em; }}
h1 {{ font-weight: 700; }} h2, h3 {{ font-weight: 600; }}
p, label, span, li {{ font-weight: 500; }}

/* sidebar */
section[data-testid="stSidebar"] {{ background: var(--mq-surface); border-right: 1px solid var(--mq-border); }}
section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] {{ border-radius: 10px; color: var(--mq-text-2); font-weight: 500; }}
section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"][aria-current="page"] {{
  background: var(--mq-accent); color: var(--mq-text); font-weight: 600; }}
.mq-brand {{ font-size: 1.25rem; font-weight: 700; padding: .25rem .25rem 1rem; letter-spacing: -0.02em; }}
.mq-brand b {{ color: var(--mq-accent); }}
.mq-sidecard {{ border: 1px solid var(--mq-border); border-radius: 12px; padding: .9rem 1rem; margin-top: 1rem; }}

/* header */
.mq-title {{ font-size: 1.6rem; font-weight: 700; margin: 0; line-height: 1.2; }}
.mq-sub {{ color: var(--mq-text-2); font-size: .9rem; margin-top: .2rem; }}
.mq-user {{ display: flex; align-items: baseline; gap: .6rem; margin-top: .3rem; }}
.mq-user-name {{ font-size: 1.15rem; font-weight: 700; color: var(--mq-text); }}
.mq-user-role {{ font-size: .8rem; font-weight: 600; color: var(--mq-text-2); border: 1px solid var(--mq-border);
  border-radius: 999px; padding: .05rem .55rem; }}
.mq-chip {{ display: inline-block; border: 1px solid var(--mq-border); background: var(--mq-surface);
  padding: .3rem .8rem; border-radius: 999px; font-size: .85rem; color: var(--mq-text-2); }}
.mq-chip strong {{ color: var(--mq-text); }}

/* cards */
.mq-card {{ background: var(--mq-surface); border: 1px solid var(--mq-border); border-radius: var(--mq-radius);
  padding: 1.5rem; box-shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.25); height: 100%; }}
.mq-card.focus {{ border-color: var(--mq-accent); box-shadow: 0 0 0 1px var(--mq-accent), 0 0 32px var(--mq-accent-glow); }}
.mq-label {{ color: var(--mq-text-2); font-size: .8rem; text-transform: uppercase; letter-spacing: .06em; font-weight: 600; }}
.mq-value {{ font-size: 2rem; font-weight: 700; margin: .4rem 0 .2rem; letter-spacing: -0.02em; }}
.mq-meta {{ color: var(--mq-meta); font-size: .8rem; }}
.mq-meta-2 {{ color: var(--mq-text-2); font-size: .85rem; }}
.mq-grid {{ display: grid; gap: 1rem; margin-bottom: 1.25rem; }}
.mq-grid.cols-4 {{ grid-template-columns: repeat(4, minmax(0,1fr)); }}
.mq-grid.cols-3 {{ grid-template-columns: repeat(3, minmax(0,1fr)); }}
.mq-section {{ margin: 1.75rem 0 .75rem; font-size: 1.05rem; font-weight: 600; }}
.mq-line-head {{ display:flex; justify-content: space-between; align-items: flex-start; gap: .5rem; }}
.mq-line-name {{ font-weight: 600; font-size: 1.05rem; }}
.mq-kv {{ display:flex; justify-content: space-between; padding: .35rem 0; border-top: 1px solid var(--mq-border); font-size: .9rem; }}
.mq-kv span:first-child {{ color: var(--mq-text-2); }}

/* status: semantic colour cue (green/yellow/red) PLUS text label and glyph, so colour is never the only signal */
.mq-pill {{ display:inline-flex; align-items:center; gap:.35rem; padding:.15rem .65rem; border-radius:999px;
  font-size:.78rem; font-weight:600; border: 1px solid var(--mq-border); color: var(--mq-text-2); white-space: nowrap; }}
.mq-pill.paid {{ color: var(--mq-green); border-color: var(--mq-green); }}
.mq-pill.due {{ color: var(--mq-red); border-color: var(--mq-red); }}
.mq-pill.overdue {{ color: var(--mq-red); border: 1px solid var(--mq-red); font-weight: 700; }}

/* tables */
.mq-table-wrap {{ overflow-x: auto; background: var(--mq-surface); border: 1px solid var(--mq-border);
  border-radius: var(--mq-radius); padding: .5rem 1rem; -webkit-overflow-scrolling: touch; }}
table.mq-table {{ width: 100%; border-collapse: collapse; font-size: .9rem; min-width: 640px; }}
table.mq-table th {{ text-align: left; color: var(--mq-text-2); font-weight: 600; font-size: .75rem;
  text-transform: uppercase; letter-spacing: .05em; padding: .8rem .6rem; border-bottom: 1px solid var(--mq-border); }}
table.mq-table td {{ padding: .85rem .6rem; border-bottom: 1px solid var(--mq-border); color: var(--mq-text); }}
table.mq-table tr:last-child td {{ border-bottom: none; }}
table.mq-table td.num, table.mq-table th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
table.mq-table td.sec {{ color: var(--mq-text-2); }}

/* notices & empty states (neutral + accent only) */
.mq-notice {{ border: 1px solid var(--mq-border); border-left: 3px solid var(--mq-accent); background: var(--mq-surface);
  border-radius: 10px; padding: .8rem 1rem; margin: .5rem 0; font-size: .92rem; }}
.mq-notice b {{ margin-right: .4rem; }}
.mq-empty {{ text-align:center; padding: 2.5rem 1rem; background: var(--mq-surface); border: 1px dashed var(--mq-meta);
  border-radius: var(--mq-radius); color: var(--mq-text-2); }}
.mq-empty h4 {{ margin: 0 0 .4rem; color: var(--mq-text); }}
.mq-skeleton {{ height: 6rem; border-radius: var(--mq-radius); background: var(--mq-surface); opacity: .6; }}

/* streamlit widgets */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
  border-radius: 10px; font-weight: 600; border: 1px solid var(--mq-border); background: var(--mq-surface); color: var(--mq-text); }}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
  background: var(--mq-accent); border-color: var(--mq-accent); color: var(--mq-text); }}
:focus-visible, button:focus-visible, input:focus-visible, [role="tab"]:focus-visible {{
  outline: 2px solid var(--mq-accent) !important; outline-offset: 2px; }}
.stTextInput input, .stNumberInput input, .stDateInput input, .stTextArea textarea, div[data-baseweb="select"] > div {{
  background: var(--mq-canvas) !important; color: var(--mq-text) !important; border-color: var(--mq-border) !important; }}
div[data-testid="stAlert"] {{ background: var(--mq-surface); border: 1px solid var(--mq-border); color: var(--mq-text); }}
div[data-testid="stExpander"] {{ background: var(--mq-surface); border: 1px solid var(--mq-border); border-radius: var(--mq-radius); }}
button[role="tab"][aria-selected="true"] {{ color: var(--mq-text); border-bottom-color: var(--mq-accent); }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: var(--mq-accent); }}
div[data-testid="stColumn"] .stButton > button {{ min-height: 2.5rem; }}
hr {{ border-color: var(--mq-border); }}

/* Shares & payments (st.columns rows): stay one row and scroll sideways instead of stacking 9 fields vertically */
.st-key-mq_shares {{ overflow-x: auto; background: var(--mq-surface); border: 1px solid var(--mq-border);
  border-radius: var(--mq-radius); padding: .5rem 1rem; -webkit-overflow-scrolling: touch; }}
.st-key-mq_shares [data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap !important; min-width: 900px;
  border-bottom: 1px solid var(--mq-border); padding: .25rem 0; }}
.st-key-mq_shares [data-testid="stColumn"] {{ min-width: 0 !important; }}
.st-key-mq_shares .mq-label {{ white-space: nowrap; font-size: .72rem; }}

/* responsive */
@media (max-width: 1024px) {{ .mq-grid.cols-4 {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
  .mq-grid.cols-3 {{ grid-template-columns: repeat(2, minmax(0,1fr)); }} }}
@media (max-width: 640px) {{
  .block-container {{ padding: 1rem 1rem 3rem; }}
  .mq-grid.cols-4, .mq-grid.cols-3 {{ grid-template-columns: 1fr; }}
  .mq-card {{ padding: 1.25rem; }} .mq-value {{ font-size: 1.6rem; }} .mq-title {{ font-size: 1.3rem; }}
}}
@media (prefers-reduced-motion: no-preference) {{ .mq-card {{ transition: box-shadow .2s ease, border-color .2s ease; }} }}
"""


def apply_theme() -> None:
    import streamlit as st

    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
