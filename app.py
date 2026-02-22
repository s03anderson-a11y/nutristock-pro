import streamlit as st
import pandas as pd
import os
import requests
import json
from datetime import datetime, timedelta

# --- KAMERA SCANNER IMPORTS ---
try:
    from pyzbar.pyzbar import decode
    from PIL import Image
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

# --- 1. SETUP & DATENSTRUKTUR ---
st.set_page_config(page_title="NutriStock Pro", layout="wide", page_icon="🥗")

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

if "recipe_items" not in st.session_state: 
    st.session_state.recipe_items = []

def init_dbs():
    if not os.path.exists(LIB_FILE):
        cols = ["Name", "Marke", "Kategorie", "Menge_Std", "Einheit_Std", "Preis"] + ALL_NUTRIENTS
        pd.DataFrame(columns=cols).to_csv(LIB_FILE, index=False)
    if not os.path.exists(DB_FILE):
        cols = ["Name", "Marke", "Menge", "Einheit", "Preis", "MHD"] + ALL_NUTRIENTS
        pd.DataFrame(columns=cols).to_csv(DB_FILE, index=False)
    if not os.path.exists(RECIPE_FILE):
        cols = ["ID", "Name", "Kategorie", "Portionen", "Gewicht_Gesamt", "Preis_Gesamt", "Zutaten_JSON"] + ALL_NUTRIENTS
        pd.DataFrame(columns=cols).to_csv(RECIPE_FILE, index=False)

init_dbs()

def load_data(file_path): 
    df = pd.read_csv(file_path)
    changed = False
    
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
        
    if changed: 
        df.to_csv(file_path, index=False)
    return df

def save_data(df, file_path): 
    if "Status" in df.columns: 
        df = df.drop(columns=["Status"])
    df.to_csv(file_path, index=False)

def to_grams(menge, einheit):
    if einheit in ["kg", "L"]: 
        return float(menge) * 1000.0
    return float(menge)

def from_grams(menge_g, ziel_einheit):
    if ziel_einheit in ["kg", "L"]: 
        return float(menge_g) / 1000.0
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
    except Exception as e:
        st.error(f"API Fehler: {e}")
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
        inv_df = pd.concat([inv_df, pd.DataFrame([new_entry])], ignore_index=True)
    return inv_df

# --- NAVIGATION ---
st.sidebar.title("🩺 NutriStock Pro")
menu = st.sidebar.radio("Navigation", [
    "🍳 Meal Creator & Rezepte", 
    "📥 Lebensmittel aufnehmen", 
    "📦 Vorratskammer", 
    "📚 Bibliothek (Stammdaten)"
])

# ==========================================
# MODUL 1: MEAL CREATOR & REZEPTE
# ==========================================
if menu == "🍳 Meal Creator & Rezepte":
    st.title("🍳 Meal Creator & Rezept-Labor")
    
    lib = load_data(LIB_FILE)
    inv = load_data(DB_FILE)
    recipes = load_data(RECIPE_FILE)
    
    tab_build, tab_match, tab_cook = st.tabs(["👨‍🍳 Rezept Baukasten", "💡 Was koche ich heute?", "📖 Meine Rezepte kochen"])
    
    # --- TAB 1: REZEPT BAUKASTEN ---
    with tab_build:
        col_search, col_joker = st.columns([2, 1])
        
        with col_search:
            st.subheader("1. Zutaten hinzufügen")
            if not lib.empty:
                lib_names = lib.apply(lambda x: f"{x['Name']} - {x['Marke']}" if pd.notna(x['Marke']) and str(x['Marke']).strip() else x['Name'], axis=1).tolist()
                selected_match = st.selectbox("🔍 Zutat tippen zum Suchen ODER Liste ausklappen:", options=["-- Bitte wählen --"] + lib_names)
                
                if selected_match != "-- Bitte wählen --":
                    sel_idx = lib_names.index(selected_match)
                    item_data = lib.iloc[sel_idx].to_dict()
                    
                    c_m, c_e, c_w = st.columns([1.5, 1, 1.5])
                    menge_input = c_m.number_input("Manuelle Menge", value=100.0, min_value=0.1)
                    einheit_input = c_e.selectbox("Einheit", UNITS, index=UNITS.index(item_data["Einheit_Std"]) if item_data["Einheit_Std"] in UNITS else 0)
                    waage_input = c_w.number_input("⚖️ Wert von Waage", value=0.0, help="Eingabe hier überschreibt die manuelle Menge.")
                    
                    final_menge = waage_input if waage_input > 0 else menge_input
                    
                    if st.button("➕ Zutat hinzufügen"):
                        menge_g = to_grams(final_menge, einheit_input)
                        base_menge = float(item_data.get("Menge_Std", 100.0))
                        base_price = float(item_data.get("Preis", 0.0))
                        
                        if item_data["Einheit_Std"] == "Stk.":
                            price_per_piece = base_price / base_menge if base_menge > 0 else 0
                            preis_anteil = price_per_piece * final_menge
                            faktor = final_menge 
                        else:
                            base_grams = to_grams(base_menge, item_data["Einheit_Std"])
                            price_per_gram = base_price / base_grams if base_grams > 0 else 0
                            preis_anteil = price_per_gram * menge_g
                            faktor = menge_g / 100.0 
                        
                        recipe_item = {
                            "Name": item_data["Name"], 
                            "Marke": item_data["Marke"], 
                            "Menge": final_menge, 
                            "Einheit": einheit_input, 
                            "Menge_Gramm": menge_g, 
                            "Preis_Anteil": preis_anteil, 
                            "Is_Joker": False
                        }
                        for n in ALL_NUTRIENTS: 
                            recipe_item[n] = float(item_data.get(n, 0)) * faktor
                        st.session_state.recipe_items.append(recipe_item)
                        st.rerun()
            else: 
                st.info("Bibliothek ist leer.")

        with col_joker:
            st.subheader("💧 Joker-Zutat")
            with st.form("joker_form"):
                j_name = st.text_input("Name (z.B. Wasser, Salz)")
                c1, c2 = st.columns(2)
                j_menge = c1.number_input("Menge", value=100.0)
                j_einh = c2.selectbox("Einheit", ["g", "ml", "L", "kg"])
                
                if st.form_submit_button("➕ Joker hinzufügen"):
                    if j_name:
                        joker_item = {
                            "Name": j_name, 
                            "Marke": "Joker", 
                            "Menge": j_menge, 
                            "Einheit": j_einh, 
                            "Menge_Gramm": to_grams(j_menge, j_einh), 
                            "Preis_Anteil": 0.0, 
                            "Is_Joker": True
                        }
                        for n in ALL_NUTRIENTS: 
                            joker_item[n] = 0.0
                        st.session_state.recipe_items.append(joker_item)
                        st.rerun()

        st.divider()
        st.subheader("2. Aktuelle Komposition & Nährwerte")
        if len(st.session_state.recipe_items) > 0:
            recipe_df = pd.DataFrame(st.session_state.recipe_items)
            display_recipe = recipe_df[["Name", "Menge", "Einheit", "Preis_Anteil", "kcal_100"]].copy()
            display_recipe.columns = ["Zutat", "Menge", "Einheit", "Kosten (€)", "Kcal"]
            st.dataframe(display_recipe, use_container_width=True)
            
            if st.button("🗑️ Liste leeren"): 
                st.session_state.recipe_items = []
                st.rerun()
            
            total_weight = recipe_df["Menge_Gramm"].sum()
            total_price = recipe_df["Preis_Anteil"].sum()
            hitze_faktor = st.checkbox("🔥 Wird dieses Gericht gekocht/erhitzt? (Simuliert Nährstoffverlust)")
            
            total_nutrients = {}
            for n in ALL_NUTRIENTS:
                val = recipe_df[n].sum()
                if hitze_faktor:
                    if n == "Vit_C": val *= 0.70 
                    elif n in ["B1", "B2", "B5", "B9"]: val *= 0.80 
                total_nutrients[n] = val

            c_port, c_info = st.columns([1, 2])
            portionen = c_port.number_input("Ergibt wie viele Portionen?", value=1, min_value=1)
            c_info.success(f"⚖️ **Gesamtgewicht:** {total_weight:.0f} g/ml | 💰 **Kosten:** {total_price:.2f} € ({total_price/portionen:.2f} € pro Portion)")

            n_tabs = st.tabs(["📊 Pro Portion", "🧪 Pro 100g (Ausbeute)", "🍲 Gesamtes Gericht"])
            with n_tabs[0]:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Kcal", f"{total_nutrients['kcal_100']/portionen:.0f}")
                m2.metric("Eiweiß", f"{total_nutrients['Prot_100']/portionen:.1f} g")
                m3.metric("Fett", f"{total_nutrients['Fett_100']/portionen:.1f} g")
                m4.metric("Carbs", f"{total_nutrients['Carb_100']/portionen:.1f} g")
            with n_tabs[1]: 
                f_100 = 100 / total_weight if total_weight > 0 else 1
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Kcal (100g)", f"{total_nutrients['kcal_100']*f_100:.0f}")
                m2.metric("Eiweiß (100g)", f"{total_nutrients['Prot_100']*f_100:.1f} g")
                m3.metric("Fett (100g)", f"{total_nutrients['Fett_100']*f_100:.1f} g")
                m4.metric("Carbs (100g)", f"{total_nutrients['Carb_100']*f_100:.1f} g")
            with n_tabs[2]: 
                st.write(f"Summe für {total_weight:.0f}g: Kcal: {total_nutrients['kcal_100']:.0f} | Eiweiß: {total_nutrients['Prot_100']:.1f}g")

            st.divider()
            with st.form("save_recipe_form"):
                r_name = st.text_input("Name des Rezepts")
                r_kat = st.selectbox("Kategorie", ["Hauptspeise", "Sauce / Basis", "Frühstück", "Snack"])
                save_to_lib = st.checkbox("🔄 Reinkarnation: Auch als Zutat in Bibliothek speichern")
                
                if st.form_submit_button("💾 Rezept speichern"):
                    if r_name:
                        zutaten_mini = [{"Name": x["Name"], "Marke": x["Marke"], "Menge_Gramm": x["Menge_Gramm"], "Einheit": x["Einheit"], "Is_Joker": x["Is_Joker"]} for x in st.session_state.recipe_items]
                        
                        new_recipe = {
                            "ID": len(recipes) + 1, 
                            "Name": r_name, 
                            "Kategorie": r_kat, 
                            "Portionen": portionen, 
                            "Gewicht_Gesamt": total_weight, 
                            "Preis_Gesamt": total_price, 
                            "Zutaten_JSON": json.dumps(zutaten_mini)
                        }
                        new_recipe.update(total_nutrients) 
                        save_data(pd.concat([recipes, pd.DataFrame([new_recipe])], ignore_index=True), RECIPE_FILE)
                        
                        if save_to_lib:
                            lib_entry = {c: 0 for c in ALL_NUTRIENTS}
                            f_100 = 100 / total_weight if total_weight > 0 else 1
                            lib_entry.update({
                                "Name": r_name, 
                                "Marke": "Hausgemacht", 
                                "Kategorie": r_kat, 
                                "Menge_Std": 100.0, 
                                "Einheit_Std": "g", 
                                "Preis": total_price * f_100
                            })
                            for n in ALL_NUTRIENTS: 
                                lib_entry[n] = total_nutrients[n] * f_100
                            save_data(pd.concat([lib, pd.DataFrame([lib_entry])], ignore_index=True), LIB_FILE)
                            
                        st.session_state.recipe_items = []
                        st.success("Rezept erfolgreich gespeichert!")
                        st.rerun()

    # --- TAB 2: WAS KOCHE ICH HEUTE? ---
    with tab_match:
        st.subheader("💡 Was kann ich mit meinem Vorrat kochen?")
        if not recipes.empty and not inv.empty:
            match_results = []
            for _, row in recipes.iterrows():
                zutaten = [z for z in json.loads(row["Zutaten_JSON"]) if not z.get("Is_Joker")]
                total_zutaten = len(zutaten)
                vorhanden_count = 0
                fehlend = []
                
                for z in zutaten:
                    v_match = inv[(inv["Name"] == z["Name"]) & (inv["Marke"] == z["Marke"])]
                    if not v_match.empty and to_grams(float(v_match.iloc[0]["Menge"]), v_match.iloc[0]["Einheit"]) >= z["Menge_Gramm"]: 
                        vorhanden_count += 1
                    else: 
                        fehlend.append(z["Name"])
                        
                score = int((vorhanden_count / total_zutaten) * 100) if total_zutaten > 0 else 0
                match_results.append({
                    "Rezept": row["Name"], 
                    "Kategorie": row["Kategorie"], 
                    "Score (%)": score, 
                    "Fehlende Zutaten": ", ".join(fehlend) if fehlend else "Alles da! 🟢"
                })
                
            match_df = pd.DataFrame(match_results).sort_values("Score (%)", ascending=False)
            st.dataframe(
                match_df, 
                use_container_width=True, 
                column_config={"Score (%)": st.column_config.ProgressColumn("Match", format="%d%%", min_value=0, max_value=100)}
            )
        else:
            st.info("Bitte speichere zuerst Rezepte und fülle deinen Vorrat.")

    # --- TAB 3: KOCHEN, RESTE & VOLLSTÄNDIGE NÄHRWERT-MATRIX ---
    with tab_cook:
        if not recipes.empty:
            selected_recipe = st.selectbox("Rezept auswählen zum Kochen:", recipes["Name"].tolist())
            
            if selected_recipe:
                r_data = recipes[recipes["Name"] == selected_recipe].iloc[0]
                zutaten = json.loads(r_data["Zutaten_JSON"])
                
                c_info1, c_info2 = st.columns(2)
                c_info1.write(f"Gesamtgewicht des Rezepts: **{r_data['Gewicht_Gesamt']:.0f}g**")
                c_info2.write(f"Ursprünglich kalkulierte Portionen: **{r_data['Portionen']}**")
                
                # Nährwert-Matrix für individuelle Portion
                with st.expander("📊 Vollständige Nährwert-Matrix anzeigen", expanded=True):
                    st.write("**Wähle deine individuelle Portionsgröße für heute:**")
                    default_portion_g = float(r_data['Gewicht_Gesamt'] / r_data['Portionen']) if r_data['Portionen'] > 0 else 100.0
                    my_portion_g = st.number_input("Meine Portionsgröße in Gramm (g)", value=default_portion_g, min_value=1.0)
                    
                    factor_portion = my_portion_g / float(r_data['Gewicht_Gesamt']) if float(r_data['Gewicht_Gesamt']) > 0 else 0
                    
                    st.write(f"Nährwerte für **{my_portion_g:.0f}g** (Zum Vergleich in Klammern: Gesamtes Gericht)")
                    matrix_tabs = st.tabs(list(NUTRIENTS.keys()))
                    
                    for i, (group, cols) in enumerate(NUTRIENTS.items()):
                        with matrix_tabs[i]:
                            l = st.columns(4)
                            for j, c_name in enumerate(cols):
                                total_val = float(r_data.get(c_name, 0.0))
                                portion_val = total_val * factor_portion
                                label = c_name.replace("_100", "")
                                l[j % 4].metric(label, f"{portion_val:.1f}", f"Gesamt: {total_val:.1f}", delta_color="off")
                
                st.write("**Vorrats-Check:**")
                kann_kochen = True
                
                for z in zutaten:
                    if z.get("Is_Joker"): 
                        continue
                    v_match = inv[(inv["Name"] == z["Name"]) & (inv["Marke"] == z["Marke"])]
                    if not v_match.empty and to_grams(float(v_match.iloc[0]["Menge"]), v_match.iloc[0]["Einheit"]) >= z["Menge_Gramm"]: 
                        st.success(f"✅ {z['Name']}: {z['Menge_Gramm']:.0f}g vorhanden")
                    else: 
                        st.warning(f"🟡 {z['Name']}: {z['Menge_Gramm']:.0f}g fehlt oder zu wenig!")
                        kann_kochen = False
                
                st.divider()
                st.subheader("Reste-Manager")
                c1, c2 = st.columns(2)
                p_eat = c1.number_input("Wie viele Portionen isst du jetzt?", value=1, min_value=1)
                p_left = c2.number_input("Reste (Portionen für Vorrat)?", value=int(r_data['Portionen'])-1, min_value=0)
                
                if st.button("🔥 Kochen, Abziehen & Reste speichern", type="primary"):
                    for z in zutaten:
                        if z.get("Is_Joker"): 
                            continue
                        idx = inv.index[(inv["Name"] == z["Name"]) & (inv["Marke"] == z["Marke"])][0]
                        neue_menge_g = to_grams(float(inv.at[idx, "Menge"]), inv.at[idx, "Einheit"]) - z["Menge_Gramm"]
                        inv.at[idx, "Menge"] = max(0.0, from_grams(neue_menge_g, inv.at[idx, "Einheit"]))
                    
                    if p_left > 0:
                        rest_g = (r_data['Gewicht_Gesamt'] / r_data['Portionen']) * p_left
                        rest_entry = {c: 0 for c in ALL_NUTRIENTS}
                        rest_entry.update({
                            "Name": f"{r_data['Name']} (Reste)", 
                            "Marke": "Gekocht", 
                            "Menge": rest_g, 
                            "Einheit": "g", 
                            "Preis": (r_data['Preis_Gesamt'] / r_data['Portionen']) * p_left, 
                            "MHD": datetime.now() + timedelta(days=3)
                        })
                        f_rest = rest_g / r_data['Gewicht_Gesamt'] if r_data['Gewicht_Gesamt'] > 0 else 0
                        for n in ALL_NUTRIENTS: 
                            rest_entry[n] = float(r_data[n]) * f_rest
                        inv = pd.concat([inv, pd.DataFrame([rest_entry])], ignore_index=True)
                    
                    save_data(inv, DB_FILE)
                    st.success("Mahlzeit! Vorrat wurde aktualisiert.")
                    st.rerun()

# ==========================================
# MODUL 2: LEBENSMITTEL AUFNEHMEN
# ==========================================
elif menu == "📥 Lebensmittel aufnehmen":
    st.title("📥 Lebensmittel in den Vorrat aufnehmen")
    inv = load_data(DB_FILE)
    lib = load_data(LIB_FILE)
    
    tab_lib, tab_scan = st.tabs(["📚 Aus bestehender Bibliothek", "📷 Barcode Scanner"])
    
    with tab_lib:
        if not lib.empty:
            lib_names = lib.apply(lambda x: f"{x['Name']} - {x['Marke']}" if pd.notna(x['Marke']) and str(x['Marke']).strip() else x['Name'], axis=1).tolist()
            selected_match = st.selectbox("🔍 Produkt tippen zum Suchen ODER Liste ausklappen:", options=["-- Bitte wählen --"] + lib_names)
            
            if selected_match != "-- Bitte wählen --":
                sel_idx = lib_names.index(selected_match)
                ref_data = lib.iloc[sel_idx].to_dict()
                
                with st.form("inv_from_lib"):
                    st.write("**Bestandsdaten & Kaufpreis**")
                    c1, c2, c3, c4 = st.columns(4)
                    i_menge = c1.number_input("Gekaufte Menge", value=float(ref_data.get('Menge_Std', 1.0)))
                    i_einheit = c2.selectbox("Einheit", UNITS, index=UNITS.index(ref_data['Einheit_Std']) if ref_data['Einheit_Std'] in UNITS else 0)
                    
                    faktor = i_menge / float(ref_data.get('Menge_Std', 1.0)) if float(ref_data.get('Menge_Std', 1.0)) > 0 else 1
                    i_preis = c3.number_input("Preis (€)", value=float(ref_data['Preis']) * faktor, format="%.2f")
                    i_mhd = c4.date_input("MHD", value=datetime.now() + timedelta(days=14))
                    
                    if st.form_submit_button("Einlagern / Bestand erhöhen"):
                        ref_data.update({
                            "Menge": i_menge, 
                            "Einheit": i_einheit, 
                            "Preis": i_preis, 
                            "MHD": i_mhd
                        })
                        for key in ["Einheit_Std", "Menge_Std"]:
                            if key in ref_data: 
                                del ref_data[key]
                        
                        save_data(add_to_inventory(inv, ref_data), DB_FILE)
                        st.success("Erfolgreich eingelagert!")
                        st.rerun()

    with tab_scan:
        st.write("Wähle deine Eingabemethode:")
        scan_method = st.radio("Methode:", ["⌨️ Tastatur-Eingabe", "📷 Kamera-Scanner"])
        barcode_value = ""
        
        if scan_method == "⌨️ Tastatur-Eingabe":
            barcode_value = st.text_input("Barcode tippen:")
            
        elif scan_method == "📷 Kamera-Scanner":
            if not PYZBAR_AVAILABLE:
                st.error("⚠️ Bitte Barcode-Paket installieren: `pip install pyzbar Pillow`.")
            else:
                img_file_buffer = st.camera_input("Halte den Barcode in die Kamera")
                if img_file_buffer is not None:
                    decoded_objects = decode(Image.open(img_file_buffer))
                    if decoded_objects:
                        barcode_value = decoded_objects[0].data.decode("utf-8")
                        st.success(f"✅ Barcode erkannt: {barcode_value}")
                    else: 
                        st.warning("⚠️ Barcode nicht erkannt. Bitte näher rangehen oder für besseres Licht sorgen.")
        
        if barcode_value:
            data = fetch_product_from_api(barcode_value)
            if data:
                with st.form("inv_from_barcode"):
                    st.write("**1. Produkt Identifikation:**")
                    c1, c2 = st.columns(2)
                    n_name = c1.text_input("Name", data['Name'])
                    n_marke = c2.text_input("Marke", data['Marke'])
                    
                    st.write("**2. Gefundene Makros bearbeiten (pro 100g/ml):**")
                    n1, n2, n3, n4, n5 = st.columns(5)
                    edit_kcal = n1.number_input("Kcal", value=float(data['kcal_100']))
                    edit_prot = n2.number_input("Eiweiß", value=float(data['Prot_100']))
                    edit_fett = n3.number_input("Fett", value=float(data['Fett_100']))
                    edit_carb = n4.number_input("Carbs", value=float(data['Carb_100']))
                    edit_fiber = n5.number_input("Ballast", value=float(data['Fiber_100']))
                    
                    st.write("**3. Alle Nährwerte (Matrix):**")
                    tabs_scan = st.tabs(list(NUTRIENTS.keys()))
                    scan_nutrients = {}
                    
                    for i, (group, cols) in enumerate(NUTRIENTS.items()):
                        with tabs_scan[i]:
                            l = st.columns(4)
                            for j, c_name in enumerate(cols):
                                default_val = edit_kcal if c_name == "kcal_100" else edit_prot if c_name == "Prot_100" else edit_fett if c_name == "Fett_100" else edit_carb if c_name == "Carb_100" else edit_fiber if c_name == "Fiber_100" else float(data.get(c_name, 0))
                                scan_nutrients[c_name] = l[j % 4].number_input(c_name.replace("_100", ""), value=default_val, key=f"scan_{c_name}")

                    st.write("**4. Bestandsdaten eingeben:**")
                    c1, c2, c3, c4 = st.columns(4)
                    i_m = c1.number_input("Gekaufte Menge", 100.0)
                    i_e = c2.selectbox("Einheit", UNITS, index=0)
                    i_p = c3.number_input("Preis (€)", 0.0)
                    i_d = c4.date_input("MHD", datetime.now() + timedelta(days=14))
                    
                    if st.form_submit_button("Speichern (Bibliothek & Vorrat)"):
                        entry = {c: 0 for c in ALL_NUTRIENTS}
                        for k, v in scan_nutrients.items(): 
                            entry[k] = v
                        entry.update({
                            "Name": n_name, 
                            "Marke": n_marke, 
                            "Preis": i_p
                        })
                        
                        lib_e = entry.copy()
                        lib_e.update({
                            "Kategorie": "Allgemein", 
                            "Menge_Std": i_m, 
                            "Einheit_Std": i_e
                        })
                        if not ((lib["Name"] == n_name) & (lib["Marke"] == n_marke)).any(): 
                            save_data(pd.concat([lib, pd.DataFrame([lib_e])], ignore_index=True), LIB_FILE)
                        
                        inv_e = entry.copy()
                        inv_e.update({
                            "Menge": i_m, 
                            "Einheit": i_e, 
                            "MHD": i_d
                        })
                        save_data(add_to_inventory(inv, inv_e), DB_FILE)
                        st.success("Erfolgreich gespeichert!")
                        st.rerun()
            else:
                st.error("Die Produktdatenbank konnte den Barcode nicht finden. Bitte manuell anlegen.")

# ==========================================
# MODUL 3: VORRATSKAMMER
# ==========================================
elif menu == "📦 Vorratskammer":
    st.title("📦 Vorratskammer")
    inv = load_data(DB_FILE)
    lib = load_data(LIB_FILE)
    
    if not inv.empty:
        today = pd.Timestamp(datetime.now().date())
        inv.insert(0, "Status", ["🔴 Leer" if float(r['Menge']) <= 0 else "🟡 Abgelaufen" if pd.to_datetime(r['MHD']) < today else "🟢 Auf Lager" for i, r in inv.iterrows()])
        
        sel = st.dataframe(
            inv[["Status", "Name", "Marke", "Menge", "Einheit", "MHD"]], 
            on_select="rerun", 
            selection_mode="single-row", 
            use_container_width=True
        )
        
        if len(sel.selection.rows) > 0:
            idx = sel.selection.rows[0]
            r = inv.iloc[idx]
            lib_match = lib[(lib["Name"] == r["Name"]) & (lib["Marke"] == r["Marke"])]
            
            st.divider()
            st.subheader(f"✏️ Bearbeitung: {r['Name']} ({r['Marke']})")
            
            tab_bestand, tab_stamm = st.tabs(["📦 Mein Bestand anpassen", "📚 Nährwerte global korrigieren"])
            
            with tab_bestand:
                with st.form("edit_inv"):
                    st.write("Bestand & Preis anpassen:")
                    c1, c2, c3, c4 = st.columns(4)
                    nm = c1.number_input("Menge", float(r['Menge']))
                    ne = c2.selectbox("Einheit", UNITS, index=UNITS.index(r['Einheit']))
                    np = c3.number_input("Preis", float(r['Preis']))
                    nd = c4.date_input("MHD", pd.to_datetime(r['MHD']))
                    
                    b1, b2, b3 = st.columns(3)
                    if b1.form_submit_button("Speichern"): 
                        inv.at[idx, 'Menge'] = nm
                        inv.at[idx, 'Einheit'] = ne
                        inv.at[idx, 'Preis'] = np
                        inv.at[idx, 'MHD'] = nd
                        save_data(inv.drop(columns=['Status']), DB_FILE)
                        st.rerun()
                        
                    if b2.form_submit_button("Leer (Setzt Menge auf 0)"): 
                        inv.at[idx, 'Menge'] = 0.0
                        save_data(inv.drop(columns=['Status']), DB_FILE)
                        st.rerun()
                        
                    if b3.form_submit_button("🗑️ Komplett löschen"): 
                        save_data(inv.drop(idx).drop(columns=['Status']), DB_FILE)
                        st.rerun()
            
            with tab_stamm:
                if not lib_match.empty:
                    lib_idx = lib_match.index[0]
                    lib_row = lib.iloc[lib_idx]
                    st.info("💡 Änderungen hier aktualisieren die Nährwerte global in deiner Bibliothek.")
                    
                    with st.form("edit_stamm_from_inv"):
                        updated_nutrients = {}
                        ntabs = st.tabs(list(NUTRIENTS.keys()))
                        
                        for i, (group, cols) in enumerate(NUTRIENTS.items()):
                            with ntabs[i]:
                                l = st.columns(4)
                                for j, c_name in enumerate(cols): 
                                    updated_nutrients[c_name] = l[j % 4].number_input(
                                        c_name.replace("_100", ""), 
                                        value=float(lib_row.get(c_name, 0)), 
                                        key=f"inv_lib_{lib_idx}_{c_name}"
                                    )
                                    
                        if st.form_submit_button("💾 Nährwerte global speichern"):
                            for k, v in updated_nutrients.items(): 
                                lib.at[lib_idx, k] = v
                                inv.at[idx, k] = v
                            save_data(lib, LIB_FILE)
                            save_data(inv.drop(columns=['Status']), DB_FILE)
                            st.success("Aktualisiert!")
                            st.rerun()

# ==========================================
# MODUL 4: BIBLIOTHEK (STAMMDATEN)
# ==========================================
elif menu == "📚 Bibliothek (Stammdaten)":
    st.title("📚 Bibliothek")
    lib = load_data(LIB_FILE)
    
    if not lib.empty:
        sel = st.dataframe(
            lib[["Name", "Marke", "Kategorie", "Menge_Std", "Einheit_Std", "Preis"]], 
            on_select="rerun", 
            selection_mode="single-row", 
            use_container_width=True
        )
        
        if len(sel.selection.rows) > 0:
            idx = sel.selection.rows[0]
            r = lib.iloc[idx]
            
            st.divider()
            with st.form("edit_lib_form"):
                st.subheader(f"✏️ Bearbeite **{r['Name']}**")
                st.write("**Stammdaten:**")
                
                c1, c2, c3 = st.columns(3)
                nn = c1.text_input("Name", r["Name"])
                n_marke = c2.text_input("Marke", r.get("Marke", ""))
                current_kat = r["Kategorie"] if pd.notna(r["Kategorie"]) else "Allgemein"
                kategorien = ["Allgemein", "Gemüse", "Obst", "Getreide", "Milchprodukte", "Fleisch", "Nüsse/Samen"]
                kat_index = kategorien.index(current_kat) if current_kat in kategorien else 0
                n_kat = c3.selectbox("Kategorie", kategorien, index=kat_index)
                
                c4, c5, c6 = st.columns(3)
                n_ms = c4.number_input("Referenzmenge", float(r.get("Menge_Std", 100)))
                es_index = UNITS.index(r["Einheit_Std"]) if r["Einheit_Std"] in UNITS else 0
                n_es = c5.selectbox("Einheit", UNITS, index=es_index)
                np = c6.number_input("Preis für diese Menge", float(r["Preis"]))
                
                st.write("**Nährwerte bearbeiten:**")
                updated_values = {}
                tabs = st.tabs(list(NUTRIENTS.keys()))
                
                for i, (group, cols) in enumerate(NUTRIENTS.items()):
                    with tabs[i]:
                        l = st.columns(4)
                        for j, c_name in enumerate(cols): 
                            updated_values[c_name] = l[j % 4].number_input(
                                c_name.replace("_100", ""), 
                                value=float(r.get(c_name, 0)), 
                                key=f"lib_edit_{idx}_{c_name}"
                            )
                            
                col_save, col_del = st.columns([1, 4])
                
                if col_save.form_submit_button("Speichern"):
                    lib.at[idx, "Name"] = nn
                    lib.at[idx, "Marke"] = n_marke
                    lib.at[idx, "Kategorie"] = n_kat
                    lib.at[idx, "Menge_Std"] = n_ms
                    lib.at[idx, "Einheit_Std"] = n_es
                    lib.at[idx, "Preis"] = np
                    for k, v in updated_values.items(): 
                        lib.at[idx, k] = v
                    save_data(lib, LIB_FILE)
                    st.success("Aktualisiert!")
                    st.rerun()
                    
                if col_del.form_submit_button("🗑️ Löschen"):
                    lib = lib.drop(idx).reset_index(drop=True)
                    save_data(lib, LIB_FILE)
                    st.rerun()
                    
    st.divider()
    with st.expander("➕ Neues Basis-Produkt manuell anlegen"):
        with st.form("new_lib_form"):
            st.write("**Stammdaten:**")
            c1, c2, c3 = st.columns(3)
            n_name = c1.text_input("Produkt")
            n_marke = c2.text_input("Marke")
            n_kat = c3.selectbox("Kategorie", ["Allgemein", "Gemüse", "Obst", "Getreide", "Milchprodukte", "Fleisch", "Nüsse/Samen"])
            
            c4, c5, c6 = st.columns(3)
            n_menge = c4.number_input("Referenzmenge", value=100.0)
            n_einh = c5.selectbox("Einheit", UNITS)
            n_preis = c6.number_input("Preis (€) für diese Menge", value=0.0)
            
            st.write("**Nährwerte eingeben:**")
            new_nutrients = {}
            tabs_new = st.tabs(list(NUTRIENTS.keys()))
            
            for i, (group, cols) in enumerate(NUTRIENTS.items()):
                with tabs_new[i]:
                    l = st.columns(4)
                    for j, c_name in enumerate(cols): 
                        new_nutrients[c_name] = l[j % 4].number_input(
                            c_name.replace("_100", ""), 
                            value=0.0, 
                            key=f"new_lib_{c_name}"
                        )
            
            if st.form_submit_button("Neues Produkt anlegen"):
                if n_name:
                    new_entry = {c: 0 for c in ALL_NUTRIENTS}
                    new_entry.update({
                        "Name": n_name, 
                        "Marke": n_marke, 
                        "Kategorie": n_kat, 
                        "Menge_Std": n_menge, 
                        "Einheit_Std": n_einh, 
                        "Preis": n_preis
                    })
                    for k, v in new_nutrients.items(): 
                        new_entry[k] = v
                        
                    if not ((lib["Name"] == n_name) & (lib["Marke"] == n_marke)).any(): 
                        save_data(pd.concat([lib, pd.DataFrame([new_entry])], ignore_index=True), LIB_FILE)
                        st.success("Neues Produkt angelegt!")
                        st.rerun()
                    else: 
                        st.error("Dieses Produkt existiert bereits in der Bibliothek.")

# === ENDE DES CODES ===