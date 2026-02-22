import pandas as pd
import requests
import json
import gspread
import streamlit as st
import difflib
from google.oauth2.service_account import Credentials
from datetime import datetime

try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False

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

@st.cache_resource
def get_gspread_client():
    creds_json = json.loads(st.secrets["google_credentials"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet():
    return get_gspread_client().open("NutriStock_DB")

def init_dbs():
    if "dbs_initialized" in st.session_state: return
    sheet = get_sheet()
    def init_tab(name, cols):
        try:
            ws = sheet.worksheet(name)
            try:
                if not ws.row_values(1): ws.insert_row(cols, index=1)
            except: ws.insert_row(cols, index=1)
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet(title=name, rows="100", cols="50")
            ws.insert_row(cols, index=1)

    init_tab(LIB_FILE, ["Name", "Marke", "Kategorie", "Menge_Std", "Einheit_Std", "Preis"] + ALL_NUTRIENTS)
    init_tab(DB_FILE, ["Name", "Marke", "Menge", "Einheit", "Preis", "MHD"] + ALL_NUTRIENTS)
    init_tab(RECIPE_FILE, ["ID", "Name", "Kategorie", "Portionen", "Gewicht_Gesamt", "Preis_Gesamt", "Zutaten_JSON", "Zubereitung"] + ALL_NUTRIENTS)
    init_tab(HISTORY_FILE, ["Datum", "Aktion", "Name", "Marke", "Menge", "Einheit", "Preis"])
    st.session_state.dbs_initialized = True

def load_data(sheet_name):
    try:
        sheet = get_sheet().worksheet(sheet_name)
        records = sheet.get_all_records()
        headers = sheet.row_values(1)
        if not records: return pd.DataFrame(columns=headers)
        df = pd.DataFrame(records)
        for col in headers:
            if col not in df.columns: df[col] = ""
        return df
    except Exception as e:
        print(f"Fehler beim Laden von {sheet_name}: {e}")
        return pd.DataFrame()

def save_data(df, sheet_name):
    if "Status" in df.columns: df = df.drop(columns=["Status"])
    df = df.fillna("")
    sheet = get_sheet().worksheet(sheet_name)
    sheet.clear()
    data = [df.columns.values.tolist()] + df.values.tolist()
    sheet.update(values=data, range_name="A1")

def log_history(aktion, name, marke, menge, einheit, preis):
    try:
        sheet = get_sheet().worksheet(HISTORY_FILE)
        datum = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([datum, aktion, name, marke, menge, einheit, preis])
    except: pass

def to_grams(menge, einheit):
    if einheit in ["kg", "L"]: return float(menge) * 1000.0
    return float(menge)

def from_grams(menge_g, ziel_einheit):
    if ziel_einheit in ["kg", "L"]: return float(menge_g) / 1000.0
    return float(menge_g)

def is_fuzzy_match(search_term, target_term, threshold=0.7):
    s1, s2 = str(search_term).lower(), str(target_term).lower()
    if s1 in s2 or s2 in s1: return True
    return difflib.SequenceMatcher(None, s1, s2).ratio() >= threshold

def translate_de_to_en(text):
    if not TRANSLATOR_AVAILABLE: return text
    try: return GoogleTranslator(source='de', target='en').translate(text)
    except: return text

def search_usda(query, api_key):
    url = f"https://api.nal.usda.gov/fdc/v1/foods/search?api_key={api_key}&query={query}&pageSize=15"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200: return resp.json().get("foods", [])
    except: pass
    return []

def get_usda_micros(fdc_id, api_key):
    url = f"https://api.nal.usda.gov/fdc/v1/food/{fdc_id}?api_key={api_key}"
    usda_map = {1087: "Calcium", 1089: "Eisen", 1090: "Magnesium", 1091: "Phosphor", 1092: "Kalium", 1093: "Natrium", 1095: "Zink", 1098: "Kupfer", 1101: "Mangan", 1103: "Selen", 1162: "Vit_C", 1165: "B1", 1166: "B2", 1167: "B3", 1170: "B5", 1175: "B6", 1177: "B9", 1178: "B12", 1106: "Vit_A", 1109: "Vit_E", 1114: "Vit_D", 1185: "Vit_K"}
    mapped = {}
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            for n in resp.json().get("foodNutrients", []):
                n_id = n.get("nutrient", {}).get("id")
                if n_id in usda_map: mapped[usda_map[n_id]] = float(n.get("amount", 0.0))
    except: pass
    return mapped

def fetch_product_from_api(barcode):
    url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200 and resp.json().get("status") == 1:
            p = resp.json()["product"]
            n = p.get("nutriments", {})
            return {"Name": p.get("product_name", "Unbekannt"), "Marke": p.get("brands", ""), "kcal_100": n.get("energy-kcal_100g", 0), "Prot_100": n.get("proteins_100g", 0), "Fett_100": n.get("fat_100g", 0), "Carb_100": n.get("carbohydrates_100g", 0), "Fiber_100": n.get("fiber_100g", 0), "Natrium": n.get("sodium_100g", 0) * 1000}
    except: pass
    return None

def add_to_inventory(inv_df, new_entry):
    log_history("Gekauft / Eingelagert", new_entry.get("Name", ""), new_entry.get("Marke", ""), new_entry.get("Menge", 0), new_entry.get("Einheit", ""), new_entry.get("Preis", 0))
    mask = (inv_df["Name"] == new_entry["Name"]) & (inv_df["Marke"] == new_entry["Marke"])
    if mask.any():
        idx = inv_df[mask].index[0]
        b_menge_g = to_grams(inv_df.at[idx, "Menge"], inv_df.at[idx, "Einheit"])
        n_menge_g = to_grams(new_entry["Menge"], new_entry["Einheit"])
        inv_df.at[idx, "Menge"] = from_grams(b_menge_g + n_menge_g, inv_df.at[idx, "Einheit"])
        inv_df.at[idx, "MHD"] = new_entry["MHD"]
        inv_df.at[idx, "Preis"] = float(inv_df.at[idx, "Preis"]) + float(new_entry["Preis"])
    else:
        for n in ALL_NUTRIENTS:
            if n not in new_entry: new_entry[n] = 0.0
        inv_df = pd.concat([inv_df, pd.DataFrame([new_entry])], ignore_index=True)
    return inv_df

def check_pantry(recipe_items_list, inv_df):
    results = []
    for item in recipe_items_list:
        if item.get("Is_Joker", False): continue
        req_g = to_grams(item["Menge"], item["Einheit"])
        avail_g = sum([to_grams(r["Menge"], r["Einheit"]) for _, r in inv_df.iterrows() if is_fuzzy_match(item["Name"], r["Name"])])
        if avail_g >= req_g: results.append({"Zutat": item["Name"], "Status": "🟢 Auf Lager", "Fehlt": "0"})
        elif avail_g > 0: results.append({"Zutat": item["Name"], "Status": "🟡 Teilweise", "Fehlt": f"{from_grams(req_g - avail_g, item['Einheit']):.2f} {item['Einheit']}"})
        else: results.append({"Zutat": item["Name"], "Status": "🔴 Fehlt komplett", "Fehlt": f"{item['Menge']} {item['Einheit']}"})
    return pd.DataFrame(results)

def deduct_cooked_recipe_from_inventory(recipe_items_list, inv_df):
    for item in recipe_items_list:
        if item.get("Is_Joker", False): continue
        req_g = to_grams(item["Menge"], item["Einheit"])
        for idx, row in inv_df.iterrows():
            if req_g <= 0: break 
            if is_fuzzy_match(item["Name"], row["Name"]):
                akt_menge_g = to_grams(row["Menge"], row["Einheit"])
                if akt_menge_g > 0:
                    abzug_g = min(req_g, akt_menge_g)
                    req_g -= abzug_g
                    inv_df.at[idx, "Menge"] = from_grams(akt_menge_g - abzug_g, row["Einheit"])
                    log_history("Gekocht (Abbuchung)", row["Name"], row["Marke"], -from_grams(abzug_g, row["Einheit"]), row["Einheit"], 0)
    return inv_df
