import pandas as pd
import os
import requests
from datetime import datetime

# --- KONSTANTEN & DATENSTRUKTUR ---
DB_FILE = "vorrat.csv"
LIB_FILE = "bibliothek.csv"
RECIPE_FILE = "rezepte.csv"

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

# --- DATENBANK FUNKTIONEN ---
def init_dbs():
    if not os.path.exists(LIB_FILE):
        cols = ["Name", "Marke", "Kategorie", "Menge_Std", "Einheit_Std", "Preis"] + ALL_NUTRIENTS
        pd.DataFrame(columns=cols).to_csv(LIB_FILE, index=False)
    if not os.path.exists(DB_FILE):
        cols = ["Name", "Marke", "Menge", "Einheit", "Preis", "MHD"] + ALL_NUTRIENTS
        pd.DataFrame(columns=cols).to_csv(DB_FILE, index=False)
    if not os.path.exists(RECIPE_FILE):
        cols = ["ID", "Name", "Kategorie", "Portionen", "Gewicht_Gesamt", "Preis_Gesamt", "Zutaten_JSON", "Zubereitung"] + ALL_NUTRIENTS
        pd.DataFrame(columns=cols).to_csv(RECIPE_FILE, index=False)

def load_data(file_path): 
    df = pd.read_csv(file_path)
    changed = False
    
    # Rückwärtskompatibilität & fehlende Spalten ergänzen
    if "Marke" not in df.columns and file_path != RECIPE_FILE: 
        df.insert(1, "Marke", "")
        changed = True
    if "Preis" not in df.columns and file_path != RECIPE_FILE: 
        df.insert(4, "Preis", 0.0)
        changed = True
    if "Menge_Std" not in df.columns and file_path == LIB_FILE: 
        df.insert(3, "Menge_Std", 100.0)
        changed = True
    if "Gewicht_Gesamt" not in df.columns and file_path == RECIPE_FILE: 
        df.insert(4, "Gewicht_Gesamt", 0.0)
        changed = True
    if "Preis_Gesamt" not in df.columns and file_path == RECIPE_FILE: 
        df.insert(5, "Preis_Gesamt", 0.0)
        changed = True
    if "Zubereitung" not in df.columns and file_path == RECIPE_FILE:
        df["Zubereitung"] = ""
        changed = True
        
    if changed: 
        df.to_csv(file_path, index=False)
    return df

def save_data(df, file_path): 
    if "Status" in df.columns: 
        df = df.drop(columns=["Status"])
    df.to_csv(file_path, index=False)

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
    """Gleicht Zutaten mit der Vorratskammer ab."""
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
