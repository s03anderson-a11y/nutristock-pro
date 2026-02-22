import pandas as pd
import requests
import json
import gspread
import streamlit as st
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- KONSTANTEN & DATENSTRUKTUR ---
DB_FILE = "Vorrat"
LIB_FILE = "Bibliothek"
RECIPE_FILE = "Rezepte"
HISTORY_FILE = "Historie"

NUTRIENTS = {
    "Makronährstoffe": ["kcal_100", "Prot_100", "Fett_100", "Carb_100", "Fiber_100"],
    "Vitamine (Fettlöslich)": ["Vit_A", "Vit_D", "Vit_E", "Vit_K"],
    "Vitamine (Wasserlöslich)": ["Vit_C", "B1", "B2", "B3", "B5", "B6", "B7", "B9", "B12"],
    "Mineralstoffe (Mengen)": ["Calcium", "Magnesium", "Kalium", "Natrium", "Chlorid", "Phosphor", "Schwefel"],
    "Mineralstoffe (Spuren)": ["Eisen", "Zink", "Jod", "Selen", "Kupfer", "Mangan", "Fluorid", "Chrom", "Molybdän"],
    "Sekundäre Pflanzenstoffe": ["Polyphenole", "Carotinoide", "Sulfide", "Glucosinolate"]
}

ALL_NUTRIENTS = [item for sub in NUTRIENTS.values() for item in sub]
UNITS = ["g", "kg", "ml", "L", "Stk."]

# --- GOOGLE SHEETS VERBINDUNG ---
@st.cache_resource
def get_gspread_client():
    creds_json = json.loads(st.secrets["google_credentials"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet():
    client = get_gspread_client()
    return client.open("NutriStock_DB")

# --- DATENBANK FUNKTIONEN ---
def init_dbs():
    # FIX: Google API Limits schützen! Führt Prüfung nur 1x pro Sitzung durch.
    if "dbs_initialized" in st.session_state:
        return
        
    sheet = get_sheet()
    
    def init_tab(name, cols):
        try:
            worksheet = sheet.worksheet(name)
            # Prüfen, ob die Tabelle wirklich ganz leer ist
            if not worksheet.get_all_values(): 
                worksheet.append_row(cols)
        except gspread.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=name, rows="100", cols="50")
            worksheet.append_row(cols)

    init_tab(LIB_FILE, ["Name", "Marke", "Kategorie", "Menge_Std", "Einheit_Std", "Preis"] + ALL_NUTRIENTS)
    init_tab(DB_FILE, ["Name", "Marke", "Menge", "Einheit", "Preis", "MHD"] + ALL_NUTRIENTS)
    init_tab(RECIPE_FILE, ["ID", "Name", "Kategorie", "Portionen", "Gewicht_Gesamt", "Preis_Gesamt", "Zutaten_JSON", "Zubereitung"] + ALL_NUTRIENTS)
    init_tab(HISTORY_FILE, ["Datum", "Aktion", "Name", "Marke", "Menge", "Einheit", "Preis"])
    
    st.session_state.dbs_initialized = True

def load_data(sheet_name):
    sheet = get_sheet().worksheet(sheet_name)
    records = sheet.get_all_records()
    headers = sheet.row_values(1)
    
    if not records:
        return pd.DataFrame(columns=headers)
        
    df = pd.DataFrame(records)
    for col in headers:
        if col not in df.columns:
            df[col] = ""
    return df

def save_data(df, sheet_name):
    if "Status" in df.columns:
        df = df.drop(columns=["Status"])
    
    # FIX: NaN-Werte für Google Sheets säubern
    df = df.fillna("")
    
    sheet = get_sheet().worksheet(sheet_name)
    sheet.clear()
    
    data = [df.columns.values.tolist()] + df.values.tolist()
    # FIX: Veraltete gspread-Syntax durch aktuelle ersetzt
    sheet.update(values=data, range_name="A1")

def log_history(aktion, name, marke, menge, einheit, preis):
    """Schreibt einen Log-Eintrag für den 10-Jahres-Tracker."""
    try:
        sheet = get_sheet().worksheet(HISTORY_FILE)
        datum = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([datum, aktion, name, marke, menge, einheit, preis])
    except Exception as e:
        print(f"Historie konnte nicht gespeichert werden: {e}")

# --- UMRECHNUNGEN & LOGIK ---
def to_grams(menge, einheit):
    if einheit in ["kg", "L"]: return float(menge) * 1000.0
    return float(menge)

def from_grams(menge_g, ziel_einheit):
    if ziel_einheit in ["kg", "L"]: return float(menge_g) / 1000.0
    return float(menge_g)

def fetch_product_from_api(barcode):
    url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == 1:
                p = data["product"]
                n = p.get("nutriments", {})
                return {
                    "Name": p.get("product_name", "Unbekannt"), 
                    "Marke": p.get("brands", ""),
                    "kcal_100": n.get("energy-kcal_100g", 0), 
                    "Prot_100": n.get("proteins_100g", 0),
                    "Fett_100": n.get("fat_100g", 0), 
                    "Carb_100": n.get("carbohydrates_100g", 0),
                    "Fiber_100": n.get("fiber_100g", 0), 
                    "Natrium": n.get("sodium_100g", 0) * 1000 
                }
    except Exception:
        pass
    return None

def add_to_inventory(inv_df, new_entry):
    log_history(
        "Gekauft / Eingelagert", 
        new_entry.get("Name", ""), 
        new_entry.get("Marke", ""), 
        new_entry.get("Menge", 0), 
        new_entry.get("Einheit", ""), 
        new_entry.get("Preis", 0)
    )
    
    mask = (inv_df["Name"] == new_entry["Name"]) & (inv_df["Marke"] == new_entry["Marke"])
    if mask.any():
        idx = inv_df[mask].index[0]
        b_menge_g = to_grams(inv_df.at[idx, "Menge"], inv_df.at[idx, "Einheit"])
        n_menge_g = to_grams(new_entry["Menge"], new_entry["Einheit"])
        
        inv_df.at[idx, "Menge"] = from_grams(b_menge_g + n_menge_g, inv_df.at[idx, "Einheit"])
        inv_df.at[idx, "MHD"] = new_entry["MHD"]
        inv_df.at[idx, "Preis"] = new_entry["Preis"]
    else:
        for n in ALL_NUTRIENTS:
            if n not in new_entry:
                new_entry[n] = 0.0
        inv_df = pd.concat([inv_df, pd.DataFrame([new_entry])], ignore_index=True)
    return inv_df

def check_pantry(recipe_items_list, inv_df):
    results = []
    for item in recipe_items_list:
        if item.get("Is_Joker", False):
            continue
            
        req_g = to_grams(item["Menge"], item["Einheit"])
        match = inv_df[inv_df["Name"].str.contains(item["Name"], case=False, na=False)]
        
        if not match.empty:
            avail_g = sum([to_grams(row["Menge"], row["Einheit"]) for _, row in match.iterrows()])
            if avail_g >= req_g:
                results.append({"Zutat": item["Name"], "Status": "🟢 Auf Lager", "Fehlt": "0"})
            else:
                fehl_g = req_g - avail_g
                results.append({"Zutat": item["Name"], "Status": "🟡 Teilweise", "Fehlt": f"{from_grams(fehl_g, item['Einheit']):.2f} {item['Einheit']}"})
        else:
            results.append({"Zutat": item["Name"], "Status": "🔴 Fehlt komplett", "Fehlt": f"{item['Menge']} {item['Einheit']}"})
    return pd.DataFrame(results)
