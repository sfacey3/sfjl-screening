import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="SFJL PEP/Sanctions Screening", layout="wide")

# Sanctions database
SANCTIONS_DB = [
    # OFAC
    {"name": "OSAMA BIN LADEN", "source": "OFAC", "type": "Individual"},
    {"name": "AYMAN AL-ZAWAHIRI", "source": "OFAC", "type": "Individual"},
    {"name": "AL QAEDA", "source": "OFAC", "type": "Organization"},
    {"name": "ISLAMIC STATE", "source": "OFAC", "type": "Organization"},
    {"name": "ISIS", "source": "OFAC", "type": "Organization"},

    # EU
    {"name": "TALIBAN", "source": "EU", "type": "Organization"},
    {"name": "HAMAS", "source": "EU", "type": "Organization"},
    {"name": "HEZBOLLAH", "source": "EU", "type": "Organization"},
    {"name": "PALESTINIAN ISLAMIC JIHAD", "source": "EU", "type": "Organization"},

    # UN
    {"name": "FARC", "source": "UN", "type": "Organization"},
    {"name": "ELN", "source": "UN", "type": "Organization"},
    {"name": "PKK", "source": "UN", "type": "Organization"},
    {"name": "YPG", "source": "UN", "type": "Organization"},

    # PEP
    {"name": "VLADIMIR PUTIN", "source": "PEP", "type": "Political Figure", "country": "Russia"},
    {"name": "NICOLAS MADURO", "source": "PEP", "type": "Political Figure", "country": "Venezuela"},
    {"name": "BASHAR AL-ASSAD", "source": "PEP", "type": "Political Figure", "country": "Syria"},
    {"name": "KIM JONG UN", "source": "PEP", "type": "Political Figure", "country": "North Korea"},
]


def normalize(text: str) -> str:
    return "".join(ch for ch in text.upper().strip() if ch.isalnum() or ch.isspace())


def screen_name(query: str, threshold: float = 0.6):
    """Return sanctions/PEP records that plausibly match the query name."""
    if not query:
        return []

    query_norm = normalize(query)
    query_tokens = set(query_norm.split())

    results = []
    for record in SANCTIONS_DB:
        record_norm = normalize(record["name"])
        record_tokens = set(record_norm.split())

        if not query_tokens or not record_tokens:
            continue

        overlap = query_tokens & record_tokens
        score = len(overlap) / max(len(query_tokens), len(record_tokens))

        is_substring = query_norm in record_norm or record_norm in query_norm

        if score >= threshold or is_substring:
            match = dict(record)
            match["match_score"] = round(max(score, 1.0 if is_substring else score), 2)
            results.append(match)

    results.sort(key=lambda r: r["match_score"], reverse=True)
    return results


def main():
    st.title("SFJL PEP / Sanctions Screening")
    st.caption("Stewart Finance Jamaica Ltd — internal screening tool. Not a substitute for a licensed sanctions data provider.")

    with st.sidebar:
        st.header("About")
        st.write(
            "This tool performs a basic name-matching screen against a small "
            "reference list of sanctioned entities and politically exposed persons (PEPs). "
            "It is intended as a preliminary screening aid only."
        )
        st.write(f"Reference list size: {len(SANCTIONS_DB)} records")
        threshold = st.slider("Match sensitivity", min_value=0.3, max_value=1.0, value=0.6, step=0.05,
                               help="Lower values return more (looser) matches.")

    st.subheader("Screen a name")
    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input("Enter a full name to screen", placeholder="e.g. John Doe")
    with col2:
        run = st.button("Screen", type="primary", use_container_width=True)

    if run or query:
        if not query:
            st.info("Enter a name above and click Screen.")
        else:
            matches = screen_name(query, threshold=threshold)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if matches:
                st.error(f"Potential match(es) found for \"{query}\": {len(matches)}")
                df = pd.DataFrame(matches)
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.success(f"No matches found for \"{query}\"")

            st.caption(f"Screened on {timestamp}")

    st.divider()
    st.subheader("Full reference list")
    st.dataframe(pd.DataFrame(SANCTIONS_DB), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
