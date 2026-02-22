import pandas as pd
import requests
import json
import gspread
import streamlit as st
import difflib
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta

# ==========================================
# KONSTANTEN & DATENSTRUKTUR
# ==========================================
DB_FILE, LIB_FILE, RECIPE_FILE, HISTORY_FILE = "Vorrat", "Bibliothek", "Rezepte", "Historie"

NUTRIENTS = {
    "Makronährstoffe": ["kcal_100", "Prot_100", "Fett_100", "Carb_100", "Fiber_100"],
    "Vitamine": ["Vit_A", "Vit_D", "Vit_E", "Vit_K", "Vit_C", "B1", "B2", "B3", "B5", "B6", "B7", "B9", "B12"],
    "Mineralstoffe": ["Calcium", "Magnesium", "Kalium", "Natrium", "Chlorid", "Phosphor", "Eisen", "Zink", "Jod", "Selen", "Kupfer", "Mangan"]
}
ALL_NUTRIENTS = [item for sub in NUTRIENTS.values() for item in sub]
UNITS = ["g", "kg", "ml", "L", "Stk."]

STD_WEIGHTS = {"zitrone": 60, "ei": 55, "apfel": 150, "banane": 120, "zwiebel": 80, "knoblauch": 5, "kartoffel": 100, "orange": 200, "tomate": 80}
MHD_DEFAULTS = {"Selbstgekocht": 4, "Fleisch": 3, "Fisch": 2, "Gemüse": 7, "Obst": 7, "Milchprodukte": 10, "Getreide": 180, "Konserve": 365, "Allgemein": 14}

# ==========================================
# GOOGLE SHEETS VERBINDUNG & DB SETUP
# ==========================================
@st.cache_resource
def get_gspread_client():
    creds = Credentials.from_service_account_info(json.loads(st.secrets["google_credentials"]), scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds)

def get_sheet(): return get_gspread_client().open("NutriStock_DB")

def init_dbs():
    if "dbs_initialized" in st.session_state: return
    sheet = get_sheet()
    def init_tab(name, cols):
        try:
            ws = sheet.worksheet(name)
            if not ws.row_values(1): ws.insert_row(cols, index=1)
        except: sheet.add_worksheet(title=name, rows="1000", cols="50").insert_row(cols, index=1)
    
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
    except: return pd.DataFrame()

def save_data(df, sheet_name):
    df_to_save = df.drop(columns=["Status", "Color"], errors="ignore").fillna("")
    ws = get_sheet().worksheet(sheet_name)
    ws.clear()
    ws.update(values=[df_to_save.columns.values.tolist()] + df_to_save.values.tolist(), range_name="A1")
    st.cache_data.clear()

def log_history(aktion, name, marke, menge, einheit, preis):
    try: get_sheet().worksheet(HISTORY_FILE).append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), aktion, name, marke, menge, einheit, preis])
    except: pass

# ==========================================
# HILFS-LOGIK
# ==========================================
def to_grams(m, e, name=""):
    try:
        m = float(m)
        if e == "Stk.":
            w = 100
            for k, v in STD_WEIGHTS.items():
                if k in str(name).lower(): w = v; break
            return m * w
        return m * 1000.0 if e in ["kg", "L"] else m
    except: return 0.0

def from_grams(m, e):
    try: return float(m) / 1000.0 if e in ["kg", "L"] else float(m)
    except: return 0.0

def predict_category(name):
    cats = {"Gemüse": ["tomate", "gurke", "zwiebel", "gemüse", "kichererbse", "bohne", "spinat", "brokkoli"], "Obst": ["apfel", "banane", "zitrone", "beere", "frucht", "orange"], "Milchprodukte": ["milch", "käse", "joghurt", "quark", "sahne"], "Fleisch": ["huhn", "rind", "schwein", "fleisch", "wurst"], "Fisch": ["lachs", "thunfisch", "fisch"], "Getreide": ["nudel", "reis", "mehl", "brot", "hafer"], "Konserve": ["dose", "konserve"], "Selbstgekocht": ["vorbereitet", "selbstgemacht", "mealprep"]}
    for c, words in cats.items():
        if any(w in str(name).lower() for w in words): return c
    return "Allgemein"

def get_mhd_default(cat): return datetime.now() + timedelta(days=MHD_DEFAULTS.get(cat, 14))

# ==========================================
# API ENGINE
# ==========================================
def fetch_comprehensive_data(barcode, api_key):
    data = {"Name": "", "Marke": "", "nutrients": {n: 0.0 for n in ALL_NUTRIENTS}}
    try:
        off = requests.get(f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json", timeout=5).json()
        if off.get("status") == 1:
            p, n = off["product"], off["product"].get("nutriments", {})
            data["Name"], data["Marke"] = p.get("product_name", ""), p.get("brands", "")
            data["nutrients"].update({"kcal_100": n.get("energy-kcal_100g", 0), "Prot_100": n.get("proteins_100g", 0), "Fett_100": n.get("fat_100g", 0), "Carb_100": n.get("carbohydrates_100g", 0), "Fiber_100": n.get("fiber_100g", 0), "Natrium": n.get("sodium_100g", 0) * 1000})
    except: pass

    q = data["Name"] if data["Name"] else barcode
    if q:
        usda = get_usda_data(q, api_key)
        for k, v in usda.items():
            if data["nutrients"].get(k, 0) == 0: data["nutrients"][k] = v
    return data

def get_usda_data(query, api_key):
    try:
        r = requests.get(f"https://api.nal.usda.gov/fdc/v1/foods/search?api_key={api_key}&query={query}&dataType=Foundation,SR%20Legacy&pageSize=1", timeout=10).json()
        if r.get("foods"):
            det = requests.get(f"https://api.nal.usda.gov/fdc/v1/food/{r['foods'][0]['fdcId']}?api_key={api_key}", timeout=10).json()
            u_map = {1008: "kcal_100", 1003: "Prot_100", 1004: "Fett_100", 1005: "Carb_100", 1079: "Fiber_100", 1087: "Calcium", 1089: "Eisen", 1090: "Magnesium", 1091: "Phosphor", 1092: "Kalium", 1093: "Natrium", 1095: "Zink", 1162: "Vit_C", 1106: "Vit_A", 1109: "Vit_E", 1114: "Vit_D", 1165: "B1", 1166: "B2", 1167: "B3", 1170: "B5", 1175: "B6", 1177: "B9", 1178: "B12"}
            res = {}
            for n in det.get("foodNutrients", []):
                if n.get("nutrient", {}).get("id") in u_map: res[u_map[n["nutrient"]["id"]]] = float(n.get("amount", 0.0))
            return res
    except: pass
    return {}

# ==========================================
# BESTANDS- & REZEPT-LOGIK
# ==========================================
def add_to_inventory(inv_df, entry):
    mask = (inv_df["Name"] == entry["Name"]) & (inv_df["Marke"] == entry["Marke"])
    if mask.any():
        idx = inv_df[mask].index[0]
        old_g = to_grams(inv_df.at[idx, "Menge"], inv_df.at[idx, "Einheit"], inv_df.at[idx, "Name"])
        new_g = to_grams(entry["Menge"], entry["Einheit"], entry["Name"])
        inv_df.at[idx, "Menge"] = from_grams(old_g + new_g, inv_df.at[idx, "Einheit"])
        inv_df.at[idx, "Preis"] = float(inv_df.at[idx, "Preis"]) + float(entry["Preis"])
        inv_df.at[idx, "MHD"] = entry["MHD"]
    else: inv_df = pd.concat([inv_df, pd.DataFrame([entry])], ignore_index=True)
    return inv_df

def delete_inventory_item(inv_df, index):
    """Löscht einen Eintrag komplett aus dem Vorrat."""
    return inv_df.drop(index).reset_index(drop=True)

def update_inventory_item(inv_df, index, new_menge):
    """Korrigiert die Menge eines Eintrags manuell."""
    old_menge = inv_df.at[index, "Menge"]
    inv_df.at[index, "Menge"] = new_menge
    # Preis anteilig anpassen
    if float(old_menge) > 0:
        inv_df.at[index, "Preis"] = (float(inv_df.at[index, "Preis"]) / float(old_menge)) * float(new_menge)
    return inv_df

def calculate_recipe_totals(zutaten_liste):
    if not zutaten_liste: return 0.0, 0.0, {n: 0.0 for n in ALL_NUTRIENTS}
    total_g = sum([to_grams(z["RezeptMenge"], z["Einheit_Std"], z["Name"]) for z in zutaten_liste])
    total_cost = 0.0
    sum_nutrients = {n: 0.0 for n in ALL_NUTRIENTS}
    
    for z in zutaten_liste:
        w_g = to_grams(z["RezeptMenge"], z["Einheit_Std"], z["Name"])
        base_g = to_grams(z["Menge_Std"], z["Einheit_Std"], z["Name"])
        if base_g > 0: total_cost += (float(z["Preis"]) / base_g) * w_g
        for n in ALL_NUTRIENTS: sum_nutrients[n] += (float(z.get(n, 0)) / 100.0) * w_g
            
    nutrients_100g = {n: (val / total_g) * 100.0 if total_g > 0 else 0 for n, val in sum_nutrients.items()}
    return total_g, total_cost, nutrients_100g

def deduct_cooked_recipe_from_inventory(zutaten_liste, inv_df, generate_shopping_list=False):
    """Zieht ab. Wenn generate_shopping_list=True, wird nichts abgebucht, sondern nur berechnet was fehlt."""
    shopping_list = []
    for z in zutaten_liste:
        needed_g = to_grams(z["RezeptMenge"], z["Einheit_Std"], z["Name"])
        for idx, row in inv_df.iterrows():
            if needed_g <= 0: break
            if difflib.SequenceMatcher(None, str(z["Name"]).lower(), str(row["Name"]).lower()).ratio() >= 0.7:
                avail_g = to_grams(row["Menge"], row["Einheit"], row["Name"])
                take_g = min(needed_g, avail_g)
                if not generate_shopping_list:
                    cost_per_g = float(row["Preis"]) / avail_g if avail_g > 0 else 0
                    inv_df.at[idx, "Preis"] = max(0, float(inv_df.at[idx, "Preis"]) - (cost_per_g * take_g))
                    inv_df.at[idx, "Menge"] = from_grams(avail_g - take_g, row["Einheit"])
                needed_g -= take_g
                if take_g > 0 and not generate_shopping_list:
                    log_history("Verbrauch", row["Name"], row["Marke"], -from_grams(take_g, row["Einheit"]), row["Einheit"], 0)
        
        # Wenn nach der Suche im Vorrat immer noch was fehlt:
        if needed_g > 0 and generate_shopping_list:
            shopping_list.append({"Name": z["Name"], "Fehlmenge": from_grams(needed_g, z["Einheit_Std"]), "Einheit": z["Einheit_Std"]})
            
    if generate_shopping_list: return shopping_list
    return inv_df

def get_stats_data(history_df):
    if history_df.empty: return pd.DataFrame()
    df = history_df.copy()
    df["Datum"] = pd.to_datetime(df["Datum"])
    df["Preis"] = pd.to_numeric(df["Preis"], errors='coerce').fillna(0)
    return df[df["Preis"] > 0]
