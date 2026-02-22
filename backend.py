import pandas as pd
import requests
import json
import gspread
import streamlit as st
import difflib
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta

# --- KONSTANTEN & DATENSTRUKTUR ---
DB_FILE, LIB_FILE, RECIPE_FILE, HISTORY_FILE = "Vorrat", "Bibliothek", "Rezepte", "Historie"

NUTRIENTS = {
    "Makronährstoffe": ["kcal_100", "Prot_100", "Fett_100", "Carb_100", "Fiber_100"],
    "Vitamine": ["Vit_A", "Vit_D", "Vit_E", "Vit_K", "Vit_C", "B1", "B2", "B3", "B5", "B6", "B7", "B9", "B12"],
    "Mineralstoffe": ["Calcium", "Magnesium", "Kalium", "Natrium", "Chlorid", "Phosphor", "Eisen", "Zink", "Jod", "Selen", "Kupfer", "Mangan"]
}
ALL_NUTRIENTS = [item for sub in NUTRIENTS.values() for item in sub]
UNITS = ["g", "kg", "ml", "L", "Stk."]

# --- LOGIK-DATENBANKEN (Vorschlag: Gewichts-Intuition & Smart Buffer) ---
STD_WEIGHTS = {
    "zitrone": 60, "ei": 55, "apfel": 150, "banane": 120, "zwiebel": 80, 
    "knoblauch": 5, "kartoffel": 100, "tomate": 80, "orange": 200
}

MHD_DEFAULTS = {
    "Selbstgekocht": 4, "Fleisch": 3, "Fisch": 2, "Gemüse": 7, "Obst": 7, 
    "Milchprodukte": 10, "Getreide": 180, "Konserve": 365, "Allgemein": 14
}

# --- GOOGLE SHEETS CONNECTION ---
@st.cache_resource
def get_gspread_client():
    try:
        creds_dict = json.loads(st.secrets["google_credentials"])
        creds = Credentials.from_service_account_info(
            creds_dict, 
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Kritischer Fehler bei der Google-Verbindung: {e}")
        return None

def get_sheet():
    client = get_gspread_client()
    return client.open("NutriStock_DB")

def init_dbs():
    if "dbs_initialized" in st.session_state: return
    sheet = get_sheet()
    def init_tab(name, cols):
        try:
            ws = sheet.worksheet(name)
            if not ws.row_values(1): ws.insert_row(cols, index=1)
        except:
            sheet.add_worksheet(title=name, rows="1000", cols="50").insert_row(cols, index=1)
    
    init_tab(LIB_FILE, ["Name", "Marke", "Kategorie", "Menge_Std", "Einheit_Std", "Preis"] + ALL_NUTRIENTS)
    init_tab(DB_FILE, ["Name", "Marke", "Menge", "Einheit", "Preis", "MHD"] + ALL_NUTRIENTS)
    init_tab(RECIPE_FILE, ["ID", "Name", "Kategorie", "Preis_Gesamt", "Gewicht_Gesamt", "Zutaten_JSON"] + ALL_NUTRIENTS)
    init_tab(HISTORY_FILE, ["Datum", "Aktion", "Name", "Marke", "Menge", "Einheit", "Preis"])
    st.session_state.dbs_initialized = True

@st.cache_data(ttl=30)
def load_data(sheet_name):
    try:
        ws = get_sheet().worksheet(sheet_name)
        records = ws.get_all_records()
        return pd.DataFrame(records) if records else pd.DataFrame(columns=ws.row_values(1))
    except:
        return pd.DataFrame()

def save_data(df, sheet_name):
    """Schreibt Daten im Batch-Verfahren (Effizienz-Vorschlag)."""
    df_to_save = df.drop(columns=["Status", "Color"], errors="ignore").fillna("")
    ws = get_sheet().worksheet(sheet_name)
    ws.clear()
    # Batch Update
    data = [df_to_save.columns.values.tolist()] + df_to_save.values.tolist()
    ws.update(values=data, range_name="A1")
    st.cache_data.clear()

def log_history(aktion, name, marke, menge, einheit, preis):
    try:
        ws = get_sheet().worksheet(HISTORY_FILE)
        ws.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), aktion, name, marke, menge, einheit, preis])
    except: pass

# --- INTELLIGENTE LOGIK-ENGINE ---
def to_grams(m, e, name=""):
    """Rechnet Einheiten präzise in Gramm um (inkl. Gewichts-Intuition)."""
    try:
        m = float(m)
        if e == "Stk.":
            weight = 100 # Fallback
            n_lower = str(name).lower()
            for key, val in STD_WEIGHTS.items():
                if key in n_lower:
                    weight = val
                    break
            return m * weight
        return m * 1000.0 if e in ["kg", "L"] else m
    except: return 0.0

def from_grams(m, e):
    """Rechnet Gramm zurück in die Ziel-Einheit."""
    try:
        m = float(m)
        return m / 1000.0 if e in ["kg", "L"] else m
    except: return 0.0

def is_fuzzy_match(search_term, target_term):
    """Erkennt Produkte auch bei leicht unterschiedlicher Schreibweise."""
    s, t = str(search_term).lower(), str(target_term).lower()
    if s in t or t in s: return True
    return difflib.SequenceMatcher(None, s, t).ratio() >= 0.7

def predict_category(name):
    """Automatische Kategorisierung basierend auf Keywords."""
    keywords = {
        "Gemüse": ["tomate", "gurke", "zwiebel", "gemüse", "kichererbse", "bohne", "spinat", "brokkoli", "paprika"],
        "Obst": ["apfel", "banane", "zitrone", "beere", "frucht", "obst", "orange", "mango"],
        "Milchprodukte": ["milch", "käse", "joghurt", "quark", "sahne", "butter"],
        "Fleisch": ["huhn", "rind", "schwein", "fleisch", "wurst", "hack", "pute"],
        "Fisch": ["lachs", "thunfisch", "fisch", "garnele", "forelle"],
        "Getreide": ["nudel", "reis", "mehl", "brot", "hafer", "quinoa", "couscous"],
        "Selbstgekocht": ["vorbereitet", "selbstgemacht", "mealprep", "rest"]
    }
    n_lower = str(name).lower()
    for cat, words in keywords.items():
        if any(w in n_lower for w in words): return cat
    return "Allgemein"

def get_mhd_default(cat):
    """Berechnet MHD-Vorschlag basierend auf der Kategorie."""
    days = MHD_DEFAULTS.get(cat, 14)
    return datetime.now() + timedelta(days=days)

# --- API & NÄHRWERT-SYNCHRONISATION ---
def fetch_comprehensive_data(barcode, api_key):
    """Bündelt OFF und USDA für lückenlose Nährwerte."""
    data = {"Name": "", "Marke": "", "nutrients": {n: 0.0 for n in ALL_NUTRIENTS}}
    
    # 1. Open Food Facts (Makros)
    try:
        off_r = requests.get(f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json", timeout=5).json()
        if off_r.get("status") == 1:
            p = off_r["product"]
            n = p.get("nutriments", {})
            data["Name"] = p.get("product_name", "")
            data["Marke"] = p.get("brands", "")
            data["nutrients"]["kcal_100"] = n.get("energy-kcal_100g", 0)
            data["nutrients"]["Prot_100"] = n.get("proteins_100g", 0)
            data["nutrients"]["Fett_100"] = n.get("fat_100g", 0)
            data["nutrients"]["Carb_100"] = n.get("carbohydrates_100g", 0)
            data["nutrients"]["Fiber_100"] = n.get("fiber_100g", 0)
    except: pass

    # 2. USDA Fallback (Mikros + fehlende Makros)
    query = data["Name"] if data["Name"] else barcode
    if query:
        usda_res = get_usda_data(query, api_key)
        for k, v in usda_res.items():
            # Übernehme Wert, wenn OFF nichts geliefert hat
            if data["nutrients"].get(k, 0) == 0:
                data["nutrients"][k] = v
                
    return data

def get_usda_data(query, api_key):
    """Holt wissenschaftliche Mikronährstoffe von USDA."""
    try:
        search_url = f"https://api.nal.usda.gov/fdc/v1/foods/search?api_key={api_key}&query={query}&dataType=Foundation,SR%20Legacy&pageSize=1"
        res = requests.get(search_url, timeout=10).json()
        if res.get("foods"):
            fdc_id = res["foods"][0]["fdcId"]
            detail_url = f"https://api.nal.usda.gov/fdc/v1/food/{fdc_id}?api_key={api_key}"
            details = requests.get(detail_url, timeout=10).json()
            
            usda_map = {
                1008: "kcal_100", 1003: "Prot_100", 1004: "Fett_100", 1005: "Carb_100", 1079: "Fiber_100",
                1087: "Calcium", 1089: "Eisen", 1090: "Magnesium", 1091: "Phosphor", 1092: "Kalium",
                1093: "Natrium", 1095: "Zink", 1162: "Vit_C", 1106: "Vit_A", 1109: "Vit_E", 1114: "Vit_D",
                1165: "B1", 1166: "B2", 1167: "B3", 1170: "B5", 1175: "B6", 1177: "B9", 1178: "B12", 1185: "Vit_K"
            }
            results = {}
            for n in details.get("foodNutrients", []):
                n_id = n.get("nutrient", {}).get("id")
                if n_id in usda_map:
                    results[usda_map[n_id]] = float(n.get("amount", 0.0))
            return results
    except: pass
    return {}

# --- BESTANDS- & REZEPT-LOGIK ---
def add_to_inventory(inv_df, entry):
    """Stacking von Beständen (Mengen & Kosten)."""
    mask = (inv_df["Name"] == entry["Name"]) & (inv_df["Marke"] == entry["Marke"])
    if mask.any():
        idx = inv_df[mask].index[0]
        old_g = to_grams(inv_df.at[idx, "Menge"], inv_df.at[idx, "Einheit"], inv_df.at[idx, "Name"])
        new_g = to_grams(entry["Menge"], entry["Einheit"], entry["Name"])
        inv_df.at[idx, "Menge"] = from_grams(old_g + new_g, inv_df.at[idx, "Einheit"])
        inv_df.at[idx, "Preis"] = float(inv_df.at[idx, "Preis"]) + float(entry["Preis"])
        inv_df.at[idx, "MHD"] = entry["MHD"]
    else:
        inv_df = pd.concat([inv_df, pd.DataFrame([entry])], ignore_index=True)
    return inv_df

def calculate_recipe_totals(zutaten_liste):
    """Berechnet Nährwerte & Kosten eines Rezepts und normiert auf 100g."""
    if not zutaten_liste: return 0, 0, {n: 0.0 for n in ALL_NUTRIENTS}
    
    total_weight_g = sum([to_grams(z["RezeptMenge"], z["Einheit_Std"], z["Name"]) for z in zutaten_liste])
    total_cost = 0.0
    sum_nutrients = {n: 0.0 for n in ALL_NUTRIENTS}
    
    for z in zutaten_liste:
        w_g = to_grams(z["RezeptMenge"], z["Einheit_Std"], z["Name"])
        # Einkaufswerte aus Bibliothek (Lib-Eintrag ist immer pro Menge_Std normiert)
        base_g = to_grams(z["Menge_Std"], z["Einheit_Std"], z["Name"])
        total_cost += (float(z["Preis"]) / base_g) * w_g if base_g > 0 else 0
        
        for n in ALL_NUTRIENTS:
            # Wert in Lib ist pro 100g -> (Wert/100) * Gramm der Zutat
            sum_nutrients[n] += (float(z.get(n, 0)) / 100.0) * w_g
            
    # Normierung des fertigen Gerichts auf 100g
    nutrients_100g = {n: (val / total_weight_g) * 100.0 if total_weight_g > 0 else 0 for n, val in sum_nutrients.items()}
    return total_weight_g, total_cost, nutrients_100g

def deduct_cooked_recipe_from_inventory(zutaten_liste, inv_df):
    """Bucht Zutaten anteilig inkl. Kosten aus dem Vorrat ab."""
    for z in zutaten_liste:
        needed_g = to_grams(z["RezeptMenge"], z["Einheit_Std"], z["Name"])
        for idx, row in inv_df.iterrows():
            if needed_g <= 0: break
            if is_fuzzy_match(z["Name"], row["Name"]):
                avail_g = to_grams(row["Menge"], row["Einheit"], row["Name"])
                take_g = min(needed_g, avail_g)
                # Kosten-Deduction: (Aktueller Preis / Gramm) * entnommene Gramm
                cost_per_g = float(row["Preis"]) / avail_g if avail_g > 0 else 0
                inv_df.at[idx, "Preis"] = max(0, float(inv_df.at[idx, "Preis"]) - (cost_per_g * take_g))
                inv_df.at[idx, "Menge"] = from_grams(avail_g - take_g, row["Einheit"])
                needed_g -= take_g
                log_history("Verbrauch", row["Name"], row["Marke"], -from_grams(take_g, row["Einheit"]), row["Einheit"], 0)
    return inv_df
