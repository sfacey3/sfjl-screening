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
    {"name": "BASHAR
