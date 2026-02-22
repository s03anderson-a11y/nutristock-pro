import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta

# --- BACKEND IMPORTIEREN ---
from backend import (
    DB_FILE, LIB_FILE, RECIPE_FILE, NUTRIENTS, ALL_NUTRIENTS, UNITS,
    init_dbs, load_data, save_data, to_grams, from_grams, log_history,
    fetch_product_from_api, add_to_inventory, check_pantry, deduct_cooked_recipe_from_inventory,
    translate_de_to_en, search_usda, get_usda_micros
)

# --- EXTERNE PAKETE PRÜFEN ---
try:
    from pyzbar.pyzbar import decode
    from PIL import Image
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

try:
    from recipe_scrapers import scrape_me
    SCRAPER_AVAILABLE = True
except ImportError:
    SCRAPER_AVAILABLE = False

# --- SETUP & SESSION STATE ---
st.set_page_config(page_title="NutriStock Pro", layout="wide", page_icon="🥗")
init_dbs()

# Initialisierung der Zwischenspeicher (Session States)
if "recipe_items" not in st.session_state: 
    st.session_state.recipe_items = []
if "recipe_instructions" not in st.session_state: 
    st.session_state.recipe_instructions = ""
if "recipe_title" not in st.session_state: 
    st.session_state.recipe_title = ""
if "api_data" not in st.session_state: 
    st.session_state.api_data = None
if "last_barcode" not in st.session_state: 
    st.session_state.last_barcode = ""
if "usda_results" not in st.session_state: 
    st.session_state.usda_results = []
if "usda_micros" not in st.session_state: 
    st.session_state.usda_micros = {}

# Globale Kategorien (inkl. "Selbstgekocht" für Mealprep)
KATEGORIEN = ["Allgemein", "Gemüse", "Obst", "Getreide", "Milchprodukte", "Fleisch", "Nüsse/Samen", "Selbstgekocht"]

# --- NAVIGATION ---
st.sidebar.title("🩺 NutriStock Pro")
menu = st.sidebar.radio(
    "Navigation", 
    ["🍳 Meal Creator & Rezepte", "📥 Lebensmittel aufnehmen", "📦 Vorratskammer", "📚 Bibliothek (Stammdaten)"]
)

# ==========================================
# MODUL 1: MEAL CREATOR & REZEPTE
# ==========================================
if menu == "🍳 Meal Creator & Rezepte":
    st.title("🍳 Meal Creator & Rezept-Labor")
    
    lib = load_data(LIB_FILE)
    inv = load_data(DB_FILE)
    recipes = load_data(RECIPE_FILE)
    
    tab_build, tab_scrape, tab_cook = st.tabs(["👨‍🍳 Rezept Baukasten", "🌐 Rezept Scraper", "📖 Meine Rezepte kochen"])
    
    # --- TAB 1: REZEPT BAUKASTEN ---
    with tab_build:
        st.subheader("1. Zutaten hinzufügen")
        col_search, col_joker = st.columns([2, 1])
        
        # Normale Zutaten aus der Bibliothek
        with col_search:
            if not lib.empty:
                lib_names = lib.apply(
                    lambda x: f"{x['Name']} - {x['Marke']}" if pd.notna(x['Marke']) and str(x['Marke']).strip() else x['Name'], 
                    axis=1
                ).tolist()
                
                selected_match = st.selectbox("🔍 Zutat aus Bibliothek wählen:", options=["-- Bitte wählen --"] + lib_names)
                
                if selected_match != "-- Bitte wählen --":
                    sel_idx = lib_names.index(selected_match)
                    item_data = lib.iloc[sel_idx].to_dict()
                    
                    c_m, c_e, c_w = st.columns([1.5, 1, 1.5])
                    menge_input = c_m.number_input("Manuelle Menge", value=0.0, min_value=0.0, step=0.1)
                    einheit_input = c_e.selectbox("Einheit", UNITS, index=UNITS.index(item_data["Einheit_Std"]) if item_data["Einheit_Std"] in UNITS else 0)
                    
                    # Hier ist der Platzhalter für die spätere Bluetooth-Waage!
                    waage_input = c_w.number_input("⚖️ Wert von Waage", value=0.0, min_value=0.0)
                    
                    final_menge = waage_input if waage_input > 0 else menge_input
                    
                    if st.button("➕ Zutat hinzufügen"):
                        if final_menge > 0:
                            menge_g = to_grams(final_menge, einheit_input)
                            base_menge = float(item_data.get("Menge_Std", 100.0))
                            base_price = float(item_data.get("Preis", 0.0))
                            
                            if item_data["Einheit_Std"] == "Stk.":
                                preis_anteil = (base_price / base_menge) * final_menge if base_menge > 0 else 0
                                faktor = final_menge 
                            else:
                                base_grams = to_grams(base_menge, item_data["Einheit_Std"])
                                preis_anteil = (base_price / base_grams) * menge_g if base_grams > 0 else 0
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
                            st.error("Bitte eine Menge > 0 angeben.")
            else: 
                st.info("Bibliothek ist leer.")

        # Joker-Zutaten (Wasser, Salz, etc. ohne Vorrats-Relevanz)
        with col_joker:
            st.subheader("💧 Joker-Zutat")
            with st.form("joker_form"):
                j_name = st.text_input("Name (z.B. Wasser, Salz)")
                c1, c2 = st.columns(2)
                j_menge = c1.number_input("Menge", value=0.0, min_value=0.0, step=0.1)
                j_einh = c2.selectbox("Einheit", UNITS)
                
                if st.form_submit_button("➕ Joker hinzufügen"):
                    if j_name and j_menge > 0:
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
                    else: 
                        st.error("Bitte Name und Menge > 0 angeben.")

        st.divider()
        st.subheader("2. Rezept überprüfen")
        
        if len(st.session_state.recipe_items) > 0:
            c_info, c_pantry = st.columns([2, 1])
            with c_info:
                df_rec = pd.DataFrame(st.session_state.recipe_items)
                display_recipe = df_rec[["Name", "Menge", "Einheit", "Preis_Anteil", "kcal_100"]].copy()
                display_recipe.columns = ["Zutat", "Menge", "Einheit", "Kosten (€)", "Kcal"]
                st.dataframe(display_recipe, use_container_width=True)
                
            with c_pantry:
                st.write("**Vorrats-Abgleich (Intelligente Suche):**")
                st.dataframe(check_pantry(st.session_state.recipe_items, inv), use_container_width=True)

            with st.form("recipe_save_form"):
                st.write("**3. Zubereitung & Portionen**")
                r_title = st.text_input("Rezept Name*", value=st.session_state.recipe_title)
                r_inst = st.text_area("Zubereitungsschritte", value=st.session_state.recipe_instructions, height=150)
                
                c_port, c_kat = st.columns(2)
                portions = c_port.number_input("Anzahl Portionen*", min_value=1.0, value=2.0, step=1.0)
                kat = c_kat.selectbox("Kategorie", ["Hauptspeise", "Frühstück", "Snack", "Dessert"])
                
                total_weight = df_rec["Menge_Gramm"].sum()
                total_price = df_rec["Preis_Anteil"].sum()
                totals = {n: df_rec[n].sum() for n in ALL_NUTRIENTS if n in df_rec.columns}
                
                c_save, c_clear = st.columns([3, 1])
                
                if c_save.form_submit_button("💾 Rezept speichern"):
                    if r_title:
                        new_rec = {
                            "ID": datetime.now().strftime("%Y%m%d%H%M%S"), 
                            "Name": r_title, 
                            "Kategorie": kat, 
                            "Portionen": portions, 
                            "Gewicht_Gesamt": total_weight, 
                            "Preis_Gesamt": total_price, 
                            "Zutaten_JSON": json.dumps(st.session_state.recipe_items), 
                            "Zubereitung": r_inst
                        }
                        for n in ALL_NUTRIENTS: 
                            new_rec[n] = totals.get(n, 0.0)
                            
                        recipes = pd.concat([recipes, pd.DataFrame([new_rec])], ignore_index=True)
                        save_data(recipes, RECIPE_FILE)
                        st.success("Rezept gespeichert!")
                        st.session_state.recipe_items = []
                        st.session_state.recipe_title = ""
                        st.session_state.recipe_instructions = ""
                        st.rerun()
                    else: 
                        st.error("Bitte einen Namen vergeben.")
                        
                if c_clear.form_submit_button("🗑️ Leeren"):
                    st.session_state.recipe_items = []
                    st.session_state.recipe_title = ""
                    st.session_state.recipe_instructions = ""
                    st.rerun()

    # --- TAB 2: REZEPT SCRAPER ---
    with tab_scrape:
        st.subheader("🌐 Rezept aus dem Internet laden")
        if not SCRAPER_AVAILABLE:
            st.error("⚠️ Paket fehlt! Bitte `recipe-scrapers` in die `requirements.txt` eintragen und Server rebooten.")
        else:
            url_input = st.text_input("URL einfügen (z.B. Chefkoch, Eatsmarter)")
            if st.button("Laden & Analysieren"):
                if url_input:
                    try:
                        with st.spinner("Lese Rezeptseite..."):
                            scraper = scrape_me(url_input)
                            st.session_state.recipe_title = scraper.title()
                            st.session_state.recipe_instructions = scraper.instructions()
                            st.success(f"Gefunden: {scraper.title()}")
                            for ing in scraper.ingredients(): 
                                st.write(f"- {ing}")
                            st.info("Wechsle jetzt zum '👨‍🍳 Rezept Baukasten'.")
                    except Exception as e:
                        st.error(f"Fehler beim Laden. Details: {e}")

    # --- TAB 3: REZEPTE KOCHEN & BATCH COOKING ---
    with tab_cook:
        if not recipes.empty:
            sel_rec = st.selectbox("Rezept wählen", recipes["Name"].tolist())
            rec_data = recipes[recipes["Name"] == sel_rec].iloc[0]
            
            total_weight_g = float(rec_data['Gewicht_Gesamt'])
            total_portions = float(rec_data['Portionen'])
            
            st.write(f"### {rec_data['Name']} (Gesamtgewicht: {total_weight_g:.0f} g / {total_portions} Portionen)")
            
            c_zutat, c_zub = st.columns(2)
            with c_zutat:
                st.write("**Zutaten (Gesamtes Rezept):**")
                zutaten_liste = []
                try: 
                    zutaten_liste = json.loads(rec_data["Zutaten_JSON"])
                except Exception: 
                    pass
                for z in zutaten_liste: 
                    st.write(f"- {z['Menge']} {z['Einheit']} {z['Name']}")
                    
            with c_zub:
                st.write("**Zubereitung:**")
                st.write(rec_data.get("Zubereitung", "Keine Anleitung hinterlegt."))
            
            st.divider()
            st.write("### 🥡 Batch-Cooking & Grammmgenaues Mealprep")
            st.write("Wie viel von dem fertig gekochten Essen (in Gramm) isst du jetzt direkt? Der Rest wandert in den Vorrat.")
            
            c_eat, c_save = st.columns(2)
            weight_per_portion = total_weight_g / total_portions if total_portions > 0 else total_weight_g
            
            # Sicherheitsnetz: Verhindert Absturz, falls Gewicht 0 ist (z.B. nur Eier/Stück verwendet)
            max_input_val = float(total_weight_g) if total_weight_g > 0 else 100.0 
            
            eaten_g = c_eat.number_input(
                "Jetzt gegessen (in Gramm):", 
                min_value=0.0, 
                max_value=max_input_val, 
                value=float(weight_per_portion), 
                step=10.0
            )
            saved_g = total_weight_g - eaten_g
            
            c_save.info(f"Es verbleiben **{saved_g:.0f} g** als Vorrat ('Selbstgekocht').")
            
            if st.button("🍳 Kochen & Zutaten abziehen"):
                # 1. Zutaten intelligent abziehen
                inv = deduct_cooked_recipe_from_inventory(zutaten_liste, inv)
                st.success("Rohe Zutaten wurden intelligent abgezogen!")
                
                # 2. Mealprep exakt berechnen und speichern (falls Reste da sind)
                if saved_g > 0:
                    meal_name = f"Vorbereitet: {rec_data['Name']}"
                    
                    # Normierung auf exakt 100g für die Bibliothek
                    factor_100g = 100.0 / total_weight_g if total_weight_g > 0 else 0
                    price_per_100g = float(rec_data['Preis_Gesamt']) * factor_100g
                    
                    meal_nutrients_100g = {}
                    for n in ALL_NUTRIENTS:
                        meal_nutrients_100g[n] = float(rec_data.get(n, 0.0)) * factor_100g
                    
                    # Bibliothekseintrag anlegen (falls noch nicht existent)
                    new_lib_entry = {
                        "Name": meal_name, "Marke": "Eigene Küche", "Kategorie": "Selbstgekocht", 
                        "Menge_Std": 100.0, "Einheit_Std": "g", "Preis": price_per_100g
                    }
                    new_lib_entry.update(meal_nutrients_100g)
                    
                    if not ((lib["Name"] == meal_name)).any():
                        lib = pd.concat([lib, pd.DataFrame([new_lib_entry])], ignore_index=True)
                        save_data(lib, LIB_FILE)
                    
                    # Vorratseintrag anlegen (Mit der echten Restmenge in Gramm)
                    actual_price = price_per_100g * (saved_g / 100.0)
                    new_inv_entry = {
                        "Name": meal_name, "Marke": "Eigene Küche", "Menge": saved_g, 
                        "Einheit": "g", "Preis": actual_price, 
                        "MHD": (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")
                    }
                    new_inv_entry.update(meal_nutrients_100g)
                    
                    inv = add_to_inventory(inv, new_inv_entry)
                    save_data(inv.drop(columns=["Status"], errors="ignore"), DB_FILE)
                    st.success(f"🥘 {saved_g:.0f} g '{meal_name}' wurden in den Vorrat gelegt (inkl. exakter Makros/Preis)!")
                
                st.rerun()
        else: 
            st.info("Noch keine Rezepte gespeichert.")


# ==========================================
# MODUL 2: LEBENSMITTEL AUFNEHMEN
# ==========================================
elif menu == "📥 Lebensmittel aufnehmen":
    st.title("📥 Lebensmittel in den Vorrat aufnehmen")
    inv, lib = load_data(DB_FILE), load_data(LIB_FILE)
    
    tab_lib, tab_scan = st.tabs(["📚 Aus bestehender Bibliothek", "📷 Barcode Scanner / Manuell"])
    
    # --- TAB 1: AUS BIBLIOTHEK ---
    with tab_lib:
        if not lib.empty:
            lib_names = lib.apply(
                lambda x: f"{x['Name']} - {x['Marke']}" if pd.notna(x['Marke']) and str(x['Marke']).strip() else x['Name'], 
                axis=1
            ).tolist()
            
            selected_match = st.selectbox("🔍 Produkt tippen zum Suchen:", options=["-- Bitte wählen --"] + lib_names, key="lib_search")
            
            if selected_match != "-- Bitte wählen --":
                sel_idx = lib_names.index(selected_match)
                ref_data = lib.iloc[sel_idx].to_dict()
                
                with st.form("inv_from_lib"):
                    c1, c2, c3, c4 = st.columns(4)
                    i_menge = c1.number_input("Menge", value=0.0, min_value=0.0, step=0.01) 
                    i_einheit = c2.selectbox("Einheit", UNITS, index=UNITS.index(ref_data['Einheit_Std']) if ref_data['Einheit_Std'] in UNITS else 0)
                    
                    faktor = i_menge / float(ref_data.get('Menge_Std', 1.0)) if float(ref_data.get('Menge_Std', 1.0)) > 0 else 1
                    i_preis = c3.number_input("Preis (€)", value=float(ref_data['Preis']) * faktor, min_value=0.0, step=0.01)
                    i_mhd = c4.date_input("MHD", value=datetime.now() + timedelta(days=14))
                    
                    if st.form_submit_button("Einlagern / Bestand erhöhen"):
                        if i_menge > 0:
                            ref_data.update({"Menge": i_menge, "Einheit": i_einheit, "Preis": i_preis, "MHD": i_mhd.strftime("%Y-%m-%d")})
                            for key in ["Einheit_Std", "Menge_Std", "Kategorie"]: 
                                ref_data.pop(key, None)
                            
                            inv = add_to_inventory(inv, ref_data)
                            save_data(inv, DB_FILE)
                            st.success(f"{i_menge} {i_einheit} erfolgreich eingelagert!")
                            st.rerun()
                        else: 
                            st.error("Bitte Menge > 0 eingeben!")

    # --- TAB 2: SCANNER & USDA API ---
    with tab_scan:
        scan_method = st.radio("Methode:", ["⌨️ Tastatur-Eingabe", "📷 Kamera-Scanner"], key="scan_radio")
        barcode_value = ""
        
        if scan_method == "⌨️ Tastatur-Eingabe":
            barcode_value = st.text_input("Barcode tippen:", key="barcode_text_input")
            
        elif scan_method == "📷 Kamera-Scanner":
            if not PYZBAR_AVAILABLE: 
                st.error("⚠️ Barcode-Paket fehlt.")
            else:
                img_file_buffer = st.file_uploader("Barcode fotografieren (Rückkamera)", type=["jpg", "jpeg", "png"], key="cam_upload")
                if img_file_buffer:
                    with st.spinner("Lese Bild..."):
                        try:
                            decoded_objects = decode(Image.open(img_file_buffer))
                            if decoded_objects:
                                barcode_value = decoded_objects[0].data.decode("utf-8")
                                st.success(f"✅ Barcode erkannt: {barcode_value}")
                            else: 
                                st.warning("⚠️ Kein Barcode gefunden.")
                        except Exception as e: 
                            st.error(f"Fehler: {e}")
        
        # Abruf der Makros über Open Food Facts
        if barcode_value and barcode_value != st.session_state.last_barcode:
            with st.spinner("Suche Makros bei Open Food Facts..."):
                st.session_state.api_data = fetch_product_from_api(barcode_value)
                st.session_state.last_barcode = barcode_value

        data = st.session_state.api_data if st.session_state.api_data else {}
        n_name_temp = data.get('Name', '')
        
        # USDA Integration
        if data or barcode_value:
            st.divider()
            st.write("### 🔬 3. Mikronährstoffe aus USDA laden")
            c_u1, c_u2 = st.columns([3, 1])
            usda_query = c_u1.text_input("Suchbegriff (Deutsch)", value=n_name_temp, key="usda_query_input")
            
            if c_u2.button("🔍 In USDA suchen", key="usda_search_btn"):
                if "usda_api_key" not in st.secrets: 
                    st.error("USDA API Key in Streamlit Secrets fehlt!")
                elif usda_query:
                    with st.spinner("Durchsuche US-Datenbank..."):
                        query_en = translate_de_to_en(usda_query)
                        st.session_state.usda_results = search_usda(query_en, st.secrets["usda_api_key"])
            
            if st.session_state.usda_results:
                options = {f"{r['description']} (FDC ID: {r['fdcId']})": r['fdcId'] for r in st.session_state.usda_results}
                sel_usda = st.selectbox("Ergebnisse:", list(options.keys()), key="usda_dropdown")
                if st.button("⬇️ Mikronährstoffe laden", key="usda_load_btn"):
                    with st.spinner("Lade USDA Daten..."):
                        st.session_state.usda_micros = get_usda_micros(options[sel_usda], st.secrets["usda_api_key"])
                        st.success("✅ Werte im Formular vorausgefüllt.")

        # Das finale Speichern-Formular
        if data or barcode_value: 
            with st.form("inv_from_barcode"):
                c1, c2 = st.columns(2)
                n_name = c1.text_input("Name", data.get('Name', ''))
                n_marke = c2.text_input("Marke", data.get('Marke', ''))
                
                st.write("**Nährwerte (pro 100g/ml):**")
                tabs_scan = st.tabs(list(NUTRIENTS.keys()))
                scan_nutrients = {}
                
                for i, (group, cols) in enumerate(NUTRIENTS.items()):
                    with tabs_scan[i]:
                        l = st.columns(4)
                        for j, c_name in enumerate(cols):
                            default_val = float(st.session_state.usda_micros.get(c_name, data.get(c_name, 0.0)))
                            scan_nutrients[c_name] = l[j % 4].number_input(
                                c_name.replace("_100", ""), 
                                value=default_val, 
                                min_value=0.0, 
                                key=f"scan_nutri_{c_name}"
                            )

                c3, c4, c5, c6 = st.columns(4)
                i_m = c3.number_input("Gekaufte Menge*", value=0.0, min_value=0.0, step=0.01) 
                i_e = c4.selectbox("Einheit*", UNITS, index=0)
                i_p = c5.number_input("Preis (€)", value=0.0, min_value=0.0, step=0.01)
                i_d = c6.date_input("MHD*", datetime.now() + timedelta(days=14))
                
                if st.form_submit_button("💾 Speichern"):
                    if n_name and i_m > 0:
                        entry = {c: 0.0 for c in ALL_NUTRIENTS}
                        for k, v in scan_nutrients.items(): 
                            entry[k] = v
                        entry.update({"Name": n_name, "Marke": n_marke, "Preis": i_p})
                        
                        lib_e = entry.copy()
                        lib_e.update({"Kategorie": "Allgemein", "Menge_Std": 100.0, "Einheit_Std": i_e})
                        
                        if not ((lib["Name"] == n_name) & (lib["Marke"] == n_marke)).any(): 
                            lib = pd.concat([lib, pd.DataFrame([lib_e])], ignore_index=True)
                            save_data(lib, LIB_FILE)
                        
                        inv_e = entry.copy()
                        inv_e.update({"Menge": i_m, "Einheit": i_e, "MHD": i_d.strftime("%Y-%m-%d")})
                        inv = add_to_inventory(inv, inv_e)
                        save_data(inv, DB_FILE)
                        
                        st.success("Erfolgreich gespeichert!")
                        st.session_state.api_data = None
                        st.session_state.last_barcode = ""
                        st.session_state.usda_micros = {}
                        st.session_state.usda_results = []
                        st.rerun()
                    else: 
                        st.error("Bitte Name und Menge > 0 eingeben!")


# ==========================================
# MODUL 3: VORRATSKAMMER
# ==========================================
elif menu == "📦 Vorratskammer":
    st.title("📦 Vorratskammer")
    inv, lib = load_data(DB_FILE), load_data(LIB_FILE)
    
    if not inv.empty:
        today = datetime.now().date()
        status_list = []
        for _, r in inv.iterrows():
            m = float(r['Menge'])
            try: 
                d = pd.to_datetime(r['MHD']).date()
            except Exception: 
                d = today + timedelta(days=365)
                
            status_list.append("🔴 Leer" if m <= 0 else "🟡 Abgelaufen" if d < today else "🟢 Auf Lager")
            
        inv.insert(0, "Status", status_list)
        
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
            tab_bestand, tab_stamm = st.tabs(["📦 Mein Bestand anpassen", "📚 Nährwerte global korrigieren"])
            
            with tab_bestand:
                c_ent1, c_ent2 = st.columns([2, 1])
                entnahme = c_ent1.number_input("Menge abziehen", min_value=0.0, step=0.01, value=0.0, key=f"ent_{idx}")
                
                if c_ent2.button("➖ Abziehen", key=f"btn_ent_{idx}"):
                    if entnahme > 0:
                        inv.at[idx, 'Menge'] = max(0.0, float(r['Menge']) - entnahme)
                        log_history("Manuelle Entnahme", r["Name"], r["Marke"], -entnahme, r["Einheit"], 0)
                        save_data(inv.drop(columns=['Status']), DB_FILE)
                        st.rerun()
                        
                with st.form("edit_inv"):
                    c1, c2, c3, c4 = st.columns(4)
                    nm = c1.number_input("Menge", value=float(r['Menge']), min_value=0.0, step=0.01) 
                    ne = c2.selectbox("Einheit", UNITS, index=UNITS.index(r['Einheit']) if r['Einheit'] in UNITS else 0)
                    np = c3.number_input("Preis", value=float(r['Preis']), min_value=0.0, step=0.01)
                    
                    try: 
                        parsed_mhd = pd.to_datetime(r['MHD'])
                    except Exception: 
                        parsed_mhd = datetime.now()
                        
                    nd = c4.date_input("MHD", parsed_mhd)
                    b1, b2, b3 = st.columns(3)
                    
                    if b1.form_submit_button("Speichern"): 
                        inv.at[idx, 'Menge'] = nm
                        inv.at[idx, 'Einheit'] = ne
                        inv.at[idx, 'Preis'] = np
                        inv.at[idx, 'MHD'] = nd.strftime("%Y-%m-%d")
                        save_data(inv.drop(columns=['Status']), DB_FILE)
                        st.rerun()
                        
                    if b3.form_submit_button("🗑️ Löschen"): 
                        save_data(inv.drop(idx).drop(columns=['Status']), DB_FILE)
                        st.rerun()
                        
            with tab_stamm:
                if not lib_match.empty:
                    lib_idx = lib_match.index[0]
                    lib_row = lib.iloc[lib_idx]
                    
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
                                        min_value=0.0, 
                                        key=f"inv_lib_{lib_idx}_{c_name}"
                                    )
                                    
                        if st.form_submit_button("💾 Speichern"):
                            for k, v in updated_nutrients.items(): 
                                lib.at[lib_idx, k] = v
                                inv.at[idx, k] = v
                            save_data(lib, LIB_FILE)
                            save_data(inv.drop(columns=['Status']), DB_FILE)
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
                c1, c2, c3 = st.columns(3)
                nn = c1.text_input("Name", r["Name"])
                n_marke = c2.text_input("Marke", r.get("Marke", ""))
                
                current_kat = r["Kategorie"] if pd.notna(r["Kategorie"]) else "Allgemein"
                n_kat = c3.selectbox("Kategorie", KATEGORIEN, index=KATEGORIEN.index(current_kat) if current_kat in KATEGORIEN else 0)
                
                c4, c5, c6 = st.columns(3)
                n_ms = c4.number_input("Referenzmenge", value=float(r.get("Menge_Std", 100)), min_value=0.0) 
                n_es = c5.selectbox("Einheit", UNITS, index=UNITS.index(r["Einheit_Std"]) if r["Einheit_Std"] in UNITS else 0)
                np = c6.number_input("Preis für diese Menge", value=float(r["Preis"]), min_value=0.0)
                
                updated_values = {}
                tabs = st.tabs(list(NUTRIENTS.keys()))
                
                for i, (group, cols) in enumerate(NUTRIENTS.items()):
                    with tabs[i]:
                        l = st.columns(4)
                        for j, c_name in enumerate(cols): 
                            updated_values[c_name] = l[j % 4].number_input(
                                c_name.replace("_100", ""), 
                                value=float(r.get(c_name, 0)), 
                                min_value=0.0, 
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
                    st.rerun()
                    
                if col_del.form_submit_button("🗑️ Löschen"):
                    lib = lib.drop(idx).reset_index(drop=True)
                    save_data(lib, LIB_FILE)
                    st.rerun()
