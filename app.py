"""
SFJL PEP / Sanctions Screening

Screens a name against real, publicly published sanctions lists (OFAC, UN,
EU, UK) and flags jurisdiction risk against the current FATF grey/black
lists. Also does a best-effort PEP check against Wikidata's record of
current heads of state/government.

IMPORTANT LIMITATIONS (see the banner in the app itself):
- There is no free, comprehensive, global PEP database. The Wikidata layer
  here only covers heads of state/government - not ministers, legislators,
  judges, state-owned-enterprise executives, or PEP family members/close
  associates.
- Sanctions lists are fetched live from official sources and cached for a
  few hours. If a source fails to load, the app says so explicitly rather
  than silently screening against a partial list.
- This tool is a screening AID for a small internal user base. It does not
  replace a licensed sanctions/PEP data vendor (World-Check, ComplyAdvantage,
  Dow Jones, LexisNexis, etc.) for regulatory/BOJ-examination purposes.
"""

import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from rapidfuzz import fuzz

st.set_page_config(page_title="SFJL PEP/Sanctions Screening", layout="wide")

HEADERS = {"User-Agent": "Mozilla/5.0 (SFJL-Compliance-Screening/1.0)"}
TIMEOUT = 25
CACHE_TTL = 12 * 60 * 60  # 12 hours

# ---------------------------------------------------------------------------
# FATF high-risk jurisdiction lists (manually maintained - FATF updates these
# at each plenary, roughly Feb / June / Oct). Last checked against the
# official FATF page: https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html
# ---------------------------------------------------------------------------
FATF_LAST_CHECKED = "2026-06-19 (FATF June 2026 plenary)"

FATF_BLACKLIST = [
    "Democratic People's Republic of Korea", "North Korea", "Iran", "Myanmar",
]

FATF_GREYLIST = [
    "Angola", "Bolivia", "Bosnia and Herzegovina", "Bulgaria", "Cameroon",
    "Côte d'Ivoire", "Democratic Republic of Congo", "Haiti", "Iraq", "Kenya",
    "Kuwait", "Lao People's Democratic Republic", "Laos", "Lebanon", "Monaco",
    "Nepal", "Papua New Guinea", "South Sudan", "Syria", "Venezuela",
    "Vietnam", "Virgin Islands (UK)", "Yemen",
]


def normalize(text: str) -> str:
    if not text:
        return ""
    return "".join(ch for ch in str(text).upper().strip() if ch.isalnum() or ch.isspace())


def strip_ns(tree: ET.Element) -> ET.Element:
    """Strip XML namespaces so plain tag lookups work regardless of the
    default namespace a source happens to declare."""
    for el in tree.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return tree


def _record(name, source, category, list_type="", country="", aliases=None, remarks=""):
    return {
        "name": name,
        "aliases": "; ".join(a for a in (aliases or []) if a),
        "source": source,
        "category": category,
        "type": list_type,
        "country": country,
        "remarks": remarks,
    }


# ---------------------------------------------------------------------------
# Source loaders. Each returns (DataFrame, status_dict). A failure in one
# source never crashes the app or silently shrinks the list - it's reported.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_ofac_sdn():
    url = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        # Legacy flat-file layout: no header row, 12 fixed columns.
        cols = ["ent_num", "sdn_name", "sdn_type", "program", "title",
                "call_sign", "vess_type", "tonnage", "grt", "vess_flag",
                "vess_owner", "remarks"]
        df = pd.read_csv(io.StringIO(resp.text), header=None, names=cols,
                          quotechar='"', on_bad_lines="skip", engine="python")
        records = [
            _record(r.sdn_name, "OFAC SDN", "Sanctions", r.sdn_type or "",
                    remarks=str(r.program or ""))
            for r in df.itertuples()
            if pd.notna(r.sdn_name)
        ]
        if not records:
            raise ValueError(f"Parsed {len(df)} row(s) but extracted 0 names")
        return pd.DataFrame(records), {"ok": True, "rows": len(records), "fetched": _now()}
    except Exception as e:
        return pd.DataFrame(), {"ok": False, "error": str(e), "fetched": _now()}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_ofac_consolidated():
    url = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_PRIM.CSV"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        cols = ["ent_num", "sdn_name", "sdn_type", "program", "title",
                "call_sign", "vess_type", "tonnage", "grt", "vess_flag",
                "vess_owner", "remarks"]
        df = pd.read_csv(io.StringIO(resp.text), header=None, names=cols,
                          quotechar='"', on_bad_lines="skip", engine="python")
        records = [
            _record(r.sdn_name, "OFAC Non-SDN Consolidated", "Sanctions", r.sdn_type or "",
                    remarks=str(r.program or ""))
            for r in df.itertuples()
            if pd.notna(r.sdn_name)
        ]
        if not records:
            raise ValueError(f"Parsed {len(df)} row(s) but extracted 0 names")
        return pd.DataFrame(records), {"ok": True, "rows": len(records), "fetched": _now()}
    except Exception as e:
        return pd.DataFrame(), {"ok": False, "error": str(e), "fetched": _now()}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_un_consolidated():
    url = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        root = strip_ns(ET.fromstring(resp.content))

        records = []
        for ind in root.iter("INDIVIDUAL"):
            def g(tag):
                el = ind.find(tag)
                return el.text.strip() if el is not None and el.text else ""

            name = " ".join(p for p in [g("FIRST_NAME"), g("SECOND_NAME"), g("THIRD_NAME"), g("FOURTH_NAME")] if p)
            aliases = [
                (a.findtext("ALIAS_NAME") or "").strip()
                for a in ind.findall("INDIVIDUAL_ALIAS")
            ]
            country = g("NATIONALITY") or g("COUNTRY")
            ref = g("REFERENCE_NUMBER")
            if name:
                records.append(_record(name, "UN Security Council", "Sanctions",
                                        "Individual", country, aliases, remarks=ref))

        for ent in root.iter("ENTITY"):
            def g(tag):
                el = ent.find(tag)
                return el.text.strip() if el is not None and el.text else ""

            name = g("FIRST_NAME")
            aliases = [
                (a.findtext("ALIAS_NAME") or "").strip()
                for a in ent.findall("ENTITY_ALIAS")
            ]
            ref = g("REFERENCE_NUMBER")
            if name:
                records.append(_record(name, "UN Security Council", "Sanctions",
                                        "Entity", "", aliases, remarks=ref))

        if not records:
            raise ValueError("Parsed XML but extracted 0 individuals/entities")

        return pd.DataFrame(records), {"ok": True, "rows": len(records), "fetched": _now()}
    except Exception as e:
        return pd.DataFrame(), {"ok": False, "error": str(e), "fetched": _now()}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_uk_list():
    url = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()

        # The published file has a metadata preamble line (e.g. "Report Date: ...")
        # before the real header row. Find the real header by looking for a
        # known official column name and skip everything above it.
        lines = resp.text.splitlines()
        header_idx = 0
        for i, line in enumerate(lines[:10]):
            if "Unique ID" in line or "Name 1" in line:
                header_idx = i
                break
        csv_text = "\n".join(lines[header_idx:])

        df = pd.read_csv(io.StringIO(csv_text), on_bad_lines="skip", engine="python")

        if len(df.columns) < 5:
            raise ValueError(
                f"Unexpected CSV shape ({len(df.columns)} column(s): {list(df.columns)[:10]}) - "
                "source may have blocked the request or changed format"
            )

        # Official UK Sanctions List field names are "Name 1".."Name 6".
        name_component_cols = [c for c in df.columns if c.strip().lower() in
                                {"name 1", "name 2", "name 3", "name 4", "name 5", "name 6"}]
        if not name_component_cols:
            name_component_cols = [
                c for c in df.columns
                if "name" in c.lower() and "type" not in c.lower() and "script" not in c.lower()
            ]
        if not name_component_cols:
            raise ValueError(f"No name columns found among: {list(df.columns)}")

        country_cols = [c for c in df.columns if "country" in c.lower() or "nationality" in c.lower()]
        type_cols = [c for c in df.columns if "individual" in c.lower() and "entity" in c.lower()]

        records = []
        for _, row in df.iterrows():
            parts = [
                str(row[c]).strip() for c in name_component_cols
                if pd.notna(row.get(c)) and str(row[c]).strip().lower() != "nan"
            ]
            name = " ".join(parts).strip()
            if not name:
                continue
            country = ""
            for c in country_cols:
                if pd.notna(row.get(c)):
                    country = str(row[c])
                    break
            list_type = ""
            for c in type_cols:
                if pd.notna(row.get(c)):
                    list_type = str(row[c])
                    break
            records.append(_record(name, "UK Sanctions List", "Sanctions", list_type, country))

        if not records:
            raise ValueError(
                f"Parsed {len(df)} row(s) but extracted 0 names from columns {name_component_cols}"
            )

        return pd.DataFrame(records), {"ok": True, "rows": len(records), "fetched": _now()}
    except Exception as e:
        return pd.DataFrame(), {"ok": False, "error": str(e), "fetched": _now()}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_eu_list():
    url = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        root = strip_ns(ET.fromstring(resp.content))

        records = []
        for entity in root.iter("sanctionEntity"):
            names = []
            for alias in entity.iter("nameAlias"):
                whole = alias.attrib.get("wholeName")
                if whole:
                    names.append(whole.strip())
            if not names:
                continue
            primary, aliases = names[0], names[1:]
            subject_type = ""
            for st_el in entity.iter("subjectType"):
                subject_type = st_el.attrib.get("classificationCode", "") or subject_type
            records.append(_record(primary, "EU Consolidated List", "Sanctions", subject_type, "", aliases))

        if not records:
            raise ValueError("XML fetched but no <sanctionEntity>/<nameAlias> nodes found - schema may have changed")

        return pd.DataFrame(records), {"ok": True, "rows": len(records), "fetched": _now()}
    except Exception as e:
        return pd.DataFrame(), {"ok": False, "error": str(e), "fetched": _now()}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_pep_wikidata():
    """Best-effort PEP layer: current heads of state / heads of government,
    sourced live from Wikidata. NOT a comprehensive PEP database - see the
    caveat banner in the UI.

    Uses P35 (head of state) and P6 (head of government), which Wikidata
    maintains directly on each country's own item as "current value"
    properties. This is deliberately NOT done by matching P39 ("position
    held") against the generic Q48352/Q2285706 concepts - in practice a
    person's P39 value is a country-specific position item (e.g. "Prime
    Minister of Jamaica"), not the generic concept itself, so that approach
    silently misses almost every country.
    """
    endpoint = "https://query.wikidata.org/sparql"
    query = """
    SELECT DISTINCT ?countryLabel ?headOfStateLabel ?headOfGovernmentLabel WHERE {
      ?country wdt:P31 wd:Q6256 .
      OPTIONAL { ?country wdt:P35 ?headOfState . }
      OPTIONAL { ?country wdt:P6 ?headOfGovernment . }
      FILTER(BOUND(?headOfState) || BOUND(?headOfGovernment))
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    LIMIT 1000
    """
    try:
        resp = requests.get(
            endpoint,
            params={"query": query, "format": "json"},
            headers={**HEADERS, "Accept": "application/sparql-results+json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        records = []
        seen = set()
        for b in data["results"]["bindings"]:
            country = b.get("countryLabel", {}).get("value", "").strip()
            head_of_state = b.get("headOfStateLabel", {}).get("value", "").strip()
            head_of_gov = b.get("headOfGovernmentLabel", {}).get("value", "").strip()

            for name, position in [(head_of_state, "Head of State"), (head_of_gov, "Head of Government")]:
                if not name:
                    continue
                key = (name, position, country)
                if key in seen:
                    continue
                seen.add(key)
                records.append(_record(name, "Wikidata (open data)", "PEP", position, country))

        if not records:
            raise ValueError("Wikidata query returned no results")

        return pd.DataFrame(records), {"ok": True, "rows": len(records), "fetched": _now()}
    except Exception as e:
        return pd.DataFrame(), {"ok": False, "error": str(e), "fetched": _now()}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


SOURCES = {
    "OFAC SDN": fetch_ofac_sdn,
    "OFAC Non-SDN Consolidated": fetch_ofac_consolidated,
    "UN Security Council": fetch_un_consolidated,
    "UK Sanctions List": fetch_uk_list,
    "EU Consolidated List": fetch_eu_list,
    "PEP (Wikidata, heads of state/government)": fetch_pep_wikidata,
}


def load_all(selected_sources):
    frames = []
    statuses = {}
    for label in selected_sources:
        df, status = SOURCES[label]()
        statuses[label] = status
        if status.get("ok") and not df.empty:
            frames.append(df)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["name", "aliases", "source", "category", "type", "country", "remarks"]
    )
    return combined, statuses


def score_match(query_norm: str, target_norm: str) -> float:
    if not query_norm or not target_norm:
        return 0.0
    return max(
        fuzz.token_sort_ratio(query_norm, target_norm),
        fuzz.WRatio(query_norm, target_norm),
    ) / 100.0


def screen_name(query: str, db: pd.DataFrame, threshold: float):
    if not query or db.empty:
        return pd.DataFrame()

    query_norm = normalize(query)
    results = []
    for row in db.itertuples(index=False):
        candidates = [row.name] + [a.strip() for a in (row.aliases or "").split(";") if a.strip()]
        best = 0.0
        for c in candidates:
            best = max(best, score_match(query_norm, normalize(c)))
        if best >= threshold:
            results.append({
                "match_score": round(best, 2),
                "matched_name": row.name,
                "source": row.source,
                "category": row.category,
                "type": row.type,
                "country": row.country,
                "aliases": row.aliases,
                "remarks": row.remarks,
            })

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results).sort_values("match_score", ascending=False)
    return out.reset_index(drop=True)


def check_country_risk(country: str):
    if not country:
        return None
    c_norm = normalize(country)
    for name in FATF_BLACKLIST:
        if normalize(name) in c_norm or c_norm in normalize(name):
            return ("black", name)
    for name in FATF_GREYLIST:
        if normalize(name) in c_norm or c_norm in normalize(name):
            return ("grey", name)
    return ("none", None)


def main():
    st.title("SFJL PEP / Sanctions Screening")
    st.caption("Stewart Finance Jamaica Ltd — internal screening tool")

    st.warning(
        "**Read before relying on this tool:** Sanctions layers (OFAC, UN, EU, UK) are "
        "fetched live from official government sources and are as current as those sources. "
        "The PEP layer is sourced from Wikidata and covers **only current heads of state and "
        "heads of government** — it does **not** cover ministers, legislators, judiciary, "
        "state-owned-enterprise executives, or PEP family members/close associates, and Wikidata "
        "itself can be incomplete, outdated, or wrong. This tool does not replace a licensed "
        "sanctions/PEP vendor (World-Check, ComplyAdvantage, Dow Jones, LexisNexis) for BOJ "
        "examination or regulatory reliance purposes. Always independently verify any hit — "
        "and any \"no match\" result on a genuinely high-risk customer.",
        icon="⚠️",
    )

    with st.sidebar:
        st.header("Data sources")
        selected_sources = st.multiselect(
            "Active sources", options=list(SOURCES.keys()), default=list(SOURCES.keys())
        )
        threshold = st.slider("Match sensitivity", min_value=0.5, max_value=1.0, value=0.75, step=0.05,
                               help="Lower values return more (looser) matches; higher values require closer matches.")
        refresh = st.button("Force refresh all sources now")
        if refresh:
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.caption(f"FATF grey/black lists last checked: {FATF_LAST_CHECKED}")

    with st.spinner("Loading sanctions and PEP data..."):
        db, statuses = load_all(selected_sources)

    with st.sidebar:
        st.subheader("Source status")
        for label, status in statuses.items():
            if status.get("ok"):
                st.success(f"{label}: {status['rows']:,} records\n\n_as of {status['fetched']}_")
            else:
                st.error(f"{label}: FAILED TO LOAD\n\n`{status.get('error', 'unknown error')}`")

    total_ok = sum(1 for s in statuses.values() if s.get("ok"))
    if total_ok < len(statuses):
        st.error(
            f"⚠️ {len(statuses) - total_ok} of {len(statuses)} data source(s) failed to load this session. "
            "Screening results below are INCOMPLETE until this is resolved — see sidebar for details."
        )

    st.subheader("Screen a name")
    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        query = st.text_input("Full name to screen", placeholder="e.g. John Doe")
    with col2:
        country = st.text_input("Country / nationality (optional, for FATF risk flag)", placeholder="e.g. Venezuela")
    with col3:
        st.write("")
        st.write("")
        run = st.button("Screen", type="primary", use_container_width=True)

    if run or query:
        if not query:
            st.info("Enter a name above and click Screen.")
        else:
            matches = screen_name(query, db, threshold)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if not matches.empty:
                st.error(f"⚠️ {len(matches)} potential match(es) found for \"{query}\"")
                st.dataframe(matches, use_container_width=True, hide_index=True)
            else:
                st.success(f"No matches found for \"{query}\" against {len(db):,} loaded records")

            risk = check_country_risk(country)
            if risk:
                level, matched = risk
                if level == "black":
                    st.error(f"🚫 Country risk: **{country}** matches the FATF black list (\"{matched}\" — Call for Action / enhanced due diligence required).")
                elif level == "grey":
                    st.warning(f"🟡 Country risk: **{country}** matches the FATF grey list (\"{matched}\" — jurisdiction under increased monitoring).")
                else:
                    st.info(f"Country risk: no FATF grey/black list match for \"{country}\".")

            st.caption(f"Screened on {timestamp} against {len(db):,} records from {total_ok} of {len(statuses)} sources")

    st.divider()
    with st.expander(f"View full loaded reference data ({len(db):,} records)"):
        st.dataframe(db, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
