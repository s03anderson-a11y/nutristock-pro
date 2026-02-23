import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from PIL import Image

# --- BACKEND IMPORT ---
from backend import (
    DB_FILE, LIB_FILE, RECIPE_FILE, HISTORY_FILE, NUTRIENTS, ALL_NUTRIENTS, UNITS,
    KATEGORIEN, MHD_DEFAULTS,
    init_dbs, load_data, save_data, log_history, to_grams, from_grams,
    predict_category, fetch_comprehensive_data, search_usda_list, get_usda_data_by_id,
    add_to_inventory, update_inventory_item, delete_inventory_item,
    calculate_recipe_totals, deduct_cooked_recipe_from_inventory, get_stats_data
)

try:
    from pyzbar.pyzbar import decode
    PYZBAR_AVAILABLE = True
except ImportError: 
    PYZBAR_AVAILABLE = False

# ==========================================
# UI SETUP & CSS SKELETON
# ==========================================
st.set_page_config(page_title="NutriStock Pro", layout="wide", page_icon="🥗")
init_dbs()

st.markdown("""
    <style>
    .card { background-color: rgba(255, 255, 255, 0.03); padding: 20px; border-radius: 12px; border-left: 5px solid #2e7d32; margin-bottom: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.2); }
    .fast-track-box { background-color: rgba(255, 255, 255, 0.02); padding: 20px; border-radius: 10px; border: 1px solid #2e7d32; margin-bottom: 20px; }
    .pantry-card { background-color: rgba(255, 255, 255, 0.03); padding: 15px; border-radius: 10px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
    .wizard-container { display: flex; justify-content: space-between; background-color: rgba(0, 0, 0, 0.2); padding: 15px; border-radius: 10px; margin-bottom: 25px; }
    .wizard-step { flex: 1; text-align: center; font-weight: bold; border-bottom: 3px solid #333; padding-bottom: 5px; opacity: 0.5; transition: opacity 0.3s; }
    .step-active { border-bottom: 3px solid #2e7d32 !important; opacity: 1; color: #2e7d32; }
    .stButton>button { border-radius: 10px; height: 3.2em; font-weight: bold; width: 100%; transition: all 0.3s; }
    .stButton>button:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(46, 125, 50, 0.4); }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# SESSION STATE MANAGEMENT
# ==========================================
if "step" not in st.session_state: st.session_state.step = 1
if "recipe_items" not in st.session_state: st.session_state.recipe_items = []
if "recipe_phase" not in st.session_state: st.session_state.recipe_phase = "build"
if "temp_nutrients" not in st.session_state: st.session_state.temp_nutrients = {n: None for n in ALL_NUTRIENTS}
if "usda_hits" not in st.session_state: st.session_state.usda_hits = []

def clear_aufnahme_session():
    st.session_state.step = 1
    st.session_state.temp_nutrients = {n: None for n in ALL_NUTRIENTS}
    st.session_state.usda_hits = []
    for key in ["t_name", "t_marke", "t_menge", "t_preis", "t_einheit", "t_mhd", "t_kat", "last_barcode"]:
        if key in st.session_state: del st.session_state[key]

# ==========================================
# SIDEBAR NAVIGATION & QUICK DEDUCT
# ==========================================
st.sidebar.title("🩺 NutriStock Pro")
menu = st.sidebar.radio("Menü", ["📥 Einkauf eintragen", "🍳 Rezept Labor", "📦 Vorrat & Inventur", "📊 Statistik", "📚 Bibliothek"])

st.sidebar.divider()
st.sidebar.subheader("⚡ Bald ablaufend")
sidebar_inv = load_data(DB_FILE).copy()

if not sidebar_inv.empty:
    sidebar_inv["MHD_Date"] = pd.to_datetime(sidebar_inv["MHD"], errors="coerce")
    expiring_items = sidebar_inv.sort_values("MHD_Date", na_position='last').head(4)
    
    for _, item in expiring_items.iterrows():
        if st.sidebar.button(f"➖ {item['Name']} verbrauchen", key=f"q_{item['Name']}"):
            unit = 1 if item['Einheit'] == "Stk." else 100
            new_inv = deduct_cooked_recipe_from_inventory([{"Name": item['Name'], "RezeptMenge": unit, "Einheit_Std": item['Einheit']}], load_data(DB_FILE).copy())
            save_data(new_inv, DB_FILE)
            st.toast(f"{unit}{item['Einheit']} {item['Name']} abgezogen!")
            st.rerun()
else:
    st.sidebar.info("Dein Vorrat ist leer.")

# ==========================================
# MODUL 1: AUFNAHME WIZARD & FAST-TRACK
# ==========================================
if menu == "📥 Einkauf eintragen":
    st.title("📥 Einkauf eintragen")
    lib, inv = load_data(LIB_FILE).copy(), load_data(DB_FILE).copy()
    
    modus = st.radio("Was möchtest du tun?", ["🔄 Bekanntes Produkt nachkaufen (Fast-Track)", "✨ Neues Produkt aufnehmen (Wizard)"], horizontal=True)
    st.divider()

    # --- FAST-TRACK ---
    if "Fast-Track" in modus:
        if lib.empty: 
            st.info("📚 Deine Bibliothek ist noch leer. Bitte nutze zuerst den Wizard für neue Produkte.")
        else:
            st.markdown("<div class='fast-track-box'>", unsafe_allow_html=True)
            with st.form("fast_track_form"):
                sel_lib = st.selectbox("Welches Produkt hast du gekauft?", sorted(lib["Name"].tolist()))
                c1, c2, c3 = st.columns(3)
                ft_menge = c1.number_input("Menge*", value=None, placeholder="Zahl...", step=0.1)
                
                ref_item = lib[lib["Name"] == sel_lib].iloc[0]
                ft_einheit = c2.selectbox("Einheit", [ref_item["Einheit_Std"]] + [u for u in UNITS if u != ref_item["Einheit_Std"]])
                ft_preis = c3.number_input("Gesamtpreis (€)*", value=None, placeholder="0.00", step=0.01)
                ft_mhd = st.date_input("MHD*", value=datetime.now() + timedelta(days=MHD_DEFAULTS.get(ref_item["Kategorie"], 14)))
                
                if st.form_submit_button("💾 Sofort Einlagern"):
                    if ft_menge and ft_preis is not None:
                        entry = {"Name": sel_lib, "Marke": ref_item["Marke"], "Menge": ft_menge, "Einheit": ft_einheit, "Preis": ft_preis, "MHD": ft_mhd.strftime("%Y-%m-%d")}
                        for n in ALL_NUTRIENTS: entry[n] = float(ref_item.get(n, 0.0))
                        save_data(add_to_inventory(inv, entry), DB_FILE)
                        log_history("Aufnahme (Fast)", entry["Name"], entry["Marke"], entry["Menge"], entry["Einheit"], entry["Preis"])
                        st.success(f"{sel_lib} erfolgreich eingelagert!"); st.rerun()
                    else: st.error("Bitte Menge und Preis angeben.")
            st.markdown("</div>", unsafe_allow_html=True)

    # --- WIZARD ---
    else:
        s1, s2, s3 = ("step-active" if st.session_state.step==1 else ""), ("step-active" if st.session_state.step==2 else ""), ("step-active" if st.session_state.step==3 else "")
        st.markdown(f"<div class='wizard-container'><div class='wizard-step {s1}'>1. Kaufdaten</div><div class='wizard-step {s2}'>2. Makros</div><div class='wizard-step {s3}'>3. Mikros</div></div>", unsafe_allow_html=True)

        # SCHRITT 1
        if st.session_state.step == 1:
            st.markdown("<div class='card'>", unsafe_allow_html=True)
            
            c_scan, c_btn = st.columns([3, 1])
            with c_scan: 
                barcode_input = st.text_input("Barcode tippen...", placeholder="13-stelligen Code eingeben")
            with c_btn:
                st.markdown("<br>", unsafe_allow_html=True) 
                search_clicked = st.button("🔍 Suchen")
            
            st.markdown("<p style='text-align: center; color: #888; font-size: 0.9em;'>ODER</p>", unsafe_allow_html=True)
            cam_img = st.camera_input("Barcode mit Kamera scannen", label_visibility="collapsed")

            barcode = None
            if cam_img and PYZBAR_AVAILABLE:
                decoded = decode(Image.open(cam_img).convert('L'))
                if decoded: barcode = decoded[0].data.decode("utf-8")
            elif search_clicked and barcode_input:
                barcode = barcode_input
            elif barcode_input and barcode_input != st.session_state.get("last_barcode"):
                barcode = barcode_input 

            if barcode and barcode != st.session_state.get("last_barcode"):
                with st.spinner("Suche in der Open Food Facts Datenbank..."):
                    usda_key = st.secrets.get("usda_api_key", "") if "usda_api_key" in st.secrets else ""
                    api_data = fetch_comprehensive_data(barcode, usda_key)
                    
                    if api_data["Name"]:
                        st.session_state.t_name, st.session_state.t_marke = api_data["Name"], api_data["Marke"]
                        for n, v in api_data["nutrients"].items(): st.session_state.temp_nutrients[n] = float(v) if v else None
                        st.toast(f"Gefunden: {api_data['Name']}", icon="✅")
                    else:
                        st.warning(f"Barcode '{barcode}' nicht gefunden. Bitte trage das Produkt manuell ein.")
                    
                    st.session_state.last_barcode = barcode

            with st.form("form_basis"):
                c_n, c_m = st.columns([2, 1])
                f_name = c_n.text_input("Name*", value=st.session_state.get("t_name", ""))
                f_marke = c_m.text_input("Marke", value=st.session_state.get("t_marke", ""))
                
                c1, c2, c3 = st.columns(3)
                f_menge = c1.number_input("Menge*", value=st.session_state.get("t_menge"), placeholder="Zahl...", step=0.1)
                old_unit_idx = UNITS.index(st.session_state.t_einheit) if "t_einheit" in st.session_state and st.session_state.t_einheit in UNITS else 0
                f_einheit = c2.selectbox("Einheit*", UNITS, index=old_unit_idx)
                f_preis = c3.number_input("Preis (€)*", value=st.session_state.get("t_preis"), placeholder="0.00", step=0.01)
                
                c4, c5 = st.columns(2)
                cat_sugg = predict_category(f_name)
                old_kat = st.session_state.get("t_kat", cat_sugg)
                old_kat_idx = KATEGORIEN.index(old_kat) if old_kat in KATEGORIEN else 0
                f_kat = c4.selectbox("Kategorie*", KATEGORIEN, index=old_kat_idx)
                f_mhd = c5.date_input("MHD*", value=st.session_state.get("t_mhd", datetime.now() + timedelta(days=MHD_DEFAULTS.get(cat_sugg, 14))))
                
                if st.form_submit_button("Weiter zu Makros ➡️"):
                    if f_name and f_menge is not None and f_preis is not None:
                        st.session_state.t_name, st.session_state.t_marke, st.session_state.t_menge = f_name, f_marke, f_menge
                        st.session_state.t_einheit, st.session_state.t_preis, st.session_state.t_mhd, st.session_state.t_kat = f_einheit, f_preis, f_mhd, f_kat
                        st.session_state.step = 2
                        st.rerun()
                    else: st.error("Bitte alle mit * markierten Felder ausfüllen.")
            st.markdown("</div>", unsafe_allow_html=True)

        # SCHRITT 2
        elif st.session_state.step == 2:
            st.subheader(f"🍎 Nährwertdeklaration für {st.session_state.t_name}")
            with st.form("form_makro"):
                st.info("Alle Angaben pro 100g / 100ml. Einfach von der Packung abtippen.")
                c_kcal, c_prot = st.columns(2)
                st.session_state.temp_nutrients["kcal_100"] = c_kcal.number_input("Energie (kcal)", value=st.session_state.temp_nutrients.get("kcal_100"), placeholder="0.0")
                st.session_state.temp_nutrients["Prot_100"] = c_prot.number_input("Eiweiß (g)", value=st.session_state.temp_nutrients.get("Prot_100"), placeholder="0.0")
                st.markdown("<hr style='margin: 10px 0; border-color: rgba(255,255,255,0.1);'>", unsafe_allow_html=True)
                
                c_f1, c_f2 = st.columns(2)
                st.session_state.temp_nutrients["Fett_100"] = c_f1.number_input("Fett gesamt (g)", value=st.session_state.temp_nutrients.get("Fett_100"), placeholder="0.0")
                st.session_state.temp_nutrients["Fett_Sat_100"] = c_f2.number_input("↳ davon gesättigte Fettsäuren (g)", value=st.session_state.temp_nutrients.get("Fett_Sat_100"), placeholder="0.0")
                st.markdown("<hr style='margin: 10px 0; border-color: rgba(255,255,255,0.1);'>", unsafe_allow_html=True)
                
                c_c1, c_c2 = st.columns(2)
                st.session_state.temp_nutrients["Carb_100"] = c_c1.number_input("Kohlenhydrate (g)", value=st.session_state.temp_nutrients.get("Carb_100"), placeholder="0.0")
                st.session_state.temp_nutrients["Zucker_100"] = c_c2.number_input("↳ davon Zucker (g)", value=st.session_state.temp_nutrients.get("Zucker_100"), placeholder="0.0")
                st.markdown("<br>", unsafe_allow_html=True)
                
                cb, cs, cn = st.columns([1, 2, 2])
                if cb.form_submit_button("⬅️ Zurück zu Schritt 1"): 
                    st.session_state.step = 1; st.rerun()
                if cs.form_submit_button("💾 Direkt Speichern"): 
                    st.session_state.do_save = True
                if cn.form_submit_button("🔬 Mikros hinzufügen ➡️"): 
                    st.session_state.step = 3; st.rerun()

        # SCHRITT 3
        elif st.session_state.step == 3:
            st.subheader("🔬 Mikronährstoffe (pro 100g)")
            st.info("Tipp: Nutze zuerst die Suche, um Werte automatisch zu laden. Fülle danach fehlende Werte manuell aus.")
            
            with st.expander("🔍 Laborwerte für generische Lebensmittel suchen (USDA)", expanded=False):
                c_sq, c_sb = st.columns([3, 1])
                usda_query = c_sq.text_input("Suchbegriff (Deutsch)", placeholder="z.B. Kokosmilch, rohe Linsen")
                
                if c_sb.button("Labor durchsuchen"):
                    if usda_query:
                        with st.spinner("Übersetze und suche im US-Labor..."):
                            usda_key = st.secrets.get("usda_api_key", "") if "usda_api_key" in st.secrets else ""
                            st.session_state.usda_hits = search_usda_list(usda_query, usda_key)
                            if not st.session_state.usda_hits: st.warning("Leider keine Treffer gefunden.")
                
                if st.session_state.get("usda_hits"):
                    opts = {f"{h['desc']} (ID: {h['id']})": h['id'] for h in st.session_state.usda_hits}
                    sel_hit = st.selectbox("Wähle den passendsten Wert:", list(opts.keys()))
                    if st.button("⬇️ Diese Mikros übernehmen"):
                        with st.spinner("Lade Detail-Nährwerte..."):
                            usda_key = st.secrets.get("usda_api_key", "") if "usda_api_key" in st.secrets else ""
                            new_micros = get_usda_data_by_id(opts[sel_hit], usda_key)
                            for k, v in new_micros.items():
                                if v > 0: st.session_state.temp_nutrients[k] = v
                            st.success("Werte erfolgreich in das Formular unten geladen!")
                            st.rerun()

            with st.form("form_mikro"):
                for g_name, items in NUTRIENTS.items():
                    if g_name == "Makronährstoffe": continue
                    st.markdown(f"**{g_name}**")
                    mcols = st.columns(4)
                    for i, item in enumerate(items):
                        st.session_state.temp_nutrients[item] = mcols[i%4].number_input(item, value=st.session_state.temp_nutrients.get(item), placeholder="0.0")
                st.markdown("<br>", unsafe_allow_html=True)
                
                cb, cs = st.columns([1, 4])
                if cb.form_submit_button("⬅️ Zurück zu Makros"): 
                    st.session_state.step = 2; st.rerun()
                if cs.form_submit_button("✅ Final Speichern & Einlagern"): 
                    st.session_state.do_save = True; st.rerun()

        # SPEICHER-LOGIK WIZARD
        if st.session_state.get("do_save"):
            entry = {"Name": st.session_state.t_name, "Marke": st.session_state.t_marke, "Menge": st.session_state.t_menge, "Einheit": st.session_state.t_einheit, "Preis": st.session_state.t_preis, "MHD": st.session_state.t_mhd.strftime("%Y-%m-%d")}
            for n in ALL_NUTRIENTS: entry[n] = float(st.session_state.temp_nutrients.get(n) or 0.0)
            save_data(add_to_inventory(inv, entry), DB_FILE)
            if not lib[lib["Name"] == entry["Name"]].any().any():
                lib_e = entry.copy(); lib_e.update({"Kategorie": st.session_state.t_kat, "Menge_Std": 100.0, "Einheit_Std": entry["Einheit"] if entry["Einheit"] != "Stk." else "Stk."})
                save_data(pd.concat([lib, pd.DataFrame([lib_e])], ignore_index=True), LIB_FILE)
            log_history("Aufnahme", entry["Name"], entry["Marke"], entry["Menge"], entry["Einheit"], entry["Preis"])
            st.success("Erfolgreich eingelagert!")
            clear_aufnahme_session(); st.rerun()

# ==========================================
# MODUL 2: REZEPT LABOR
# ==========================================
elif menu == "🍳 Rezept Labor":
    st.title("🍳 Rezept-Labor")
    lib, inv = load_data(LIB_FILE).copy(), load_data(DB_FILE).copy()

    if lib.empty:
        st.info("📚 Bitte lege zuerst Lebensmittel über den 'Aufnahme Wizard' an, bevor du Rezepte erstellst.")
    else:
        if st.session_state.recipe_phase == "build":
            st.markdown("<div class='card'>", unsafe_allow_html=True)
            c_sel, c_qty, c_add = st.columns([3, 1, 1])
            sel_item = c_sel.selectbox("Zutat aus Bibliothek", ["--"] + sorted(lib["Name"].tolist()))
            qty_item = c_qty.number_input("Menge", value=None, placeholder="z.B. 150")
            
            if c_add.button("➕ Hinzufügen"):
                if sel_item != "--" and qty_item is not None:
                    existing_idx = next((i for i, item in enumerate(st.session_state.recipe_items) if item["Name"] == sel_item), None)
                    if existing_idx is not None:
                        st.session_state.recipe_items[existing_idx]["RezeptMenge"] += float(qty_item)
                        st.toast(f"Menge aktualisiert!")
                    else:
                        details = lib[lib["Name"] == sel_item].iloc[0].to_dict()
                        details["RezeptMenge"] = float(qty_item)
                        st.session_state.recipe_items.append(details)
                        st.toast(f"{sel_item} hinzugefügt!")
                    st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

            if st.session_state.recipe_items:
                st.subheader("📋 Meine Zutaten")
                for i, item in enumerate(st.session_state.recipe_items):
                    colz = st.columns([4, 1])
                    colz[0].markdown(f"**{item['RezeptMenge']} {item['Einheit_Std']}** {item['Name']}")
                    if colz[1].button("🗑️", key=f"del_{i}"): 
                        st.session_state.recipe_items.pop(i); st.rerun()
                
                st.divider()
                c_check, c_finish = st.columns(2)
                if c_check.button("🛒 Einkaufsliste prüfen"):
                    missing = deduct_cooked_recipe_from_inventory(st.session_state.recipe_items, inv, generate_shopping_list=True)
                    if missing:
                        st.warning("⚠️ Folgende Zutaten fehlen im Vorrat:")
                        st.dataframe(pd.DataFrame(missing))
                    else: st.success("✅ Alle Zutaten sind ausreichend vorhanden!")
                if c_finish.button("🏁 Rezept fertigstellen"): 
                    st.session_state.recipe_phase = "summary"; st.rerun()

        elif st.session_state.recipe_phase == "summary":
            st.subheader("📊 Zusammenfassung & Speichern")
            scaler = st.slider("Personen/Portionen anpassen", 0.5, 5.0, 1.0, 0.5)
            w, cost, nutris = calculate_recipe_totals(st.session_state.recipe_items)
            
            # Skalierte Ansicht für den User
            w_scaled, cost_scaled = w * scaler, cost * scaler

            all_micros = NUTRIENTS["Vitamine"] + NUTRIENTS["Mineralstoffe"]
            available_micros = {k: nutris.get(k, 0) for k in all_micros if nutris.get(k, 0) > 0}
            sorted_micros = sorted(available_micros.items(), key=lambda item: item[1], reverse=True)
            
            if len(sorted_micros) >= 3:
                r_keys = [k for k, v in sorted_micros[:8]]
                r_vals = [v for k, v in sorted_micros[:8]]
            else:
                r_keys = NUTRIENTS["Mineralstoffe"][:8]
                r_vals = [nutris.get(k, 0) for k in r_keys]

            fig = go.Figure(data=go.Scatterpolar(r=r_vals, theta=r_keys, fill='toself', line_color='#2e7d32'))
            fig.update_layout(polar=dict(radialaxis=dict(visible=False)), showlegend=False, height=350, margin=dict(t=20, b=20), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
            
            c_chart, c_data = st.columns([1, 1])
            c_chart.plotly_chart(fig, use_container_width=True)
            c_data.markdown(f"<div class='card'><b>Gewicht gesamt:</b> {w_scaled:.0f}g<br><b>Kosten gesamt:</b> {cost_scaled:.2f}€<br><b>Kalorien (pro 100g):</b> {nutris['kcal_100']:.0f} kcal<br><b>Zucker (pro 100g):</b> {nutris['Zucker_100']:.1f} g</div>", unsafe_allow_html=True)

            with st.form("recipe_finish_form"):
                r_name = st.text_input("Name für Mealprep*", placeholder="z.B. Linsen-Dal")
                eat_now = st.number_input("Wie viel g isst du jetzt direkt davon?", value=None, placeholder="0.0")
                c_back, c_save = st.columns(2)
                
                if c_back.form_submit_button("⬅️ Zurück zum Bearbeiten"): 
                    st.session_state.recipe_phase = "build"; st.rerun()
                
                if c_save.form_submit_button("🚀 Kochen & Mealprep anlegen"):
                    if r_name:
                        eat_g = float(eat_now) if eat_now else 0.0
                        if eat_g > w_scaled: 
                            st.error("Du kannst nicht mehr essen, als du gekocht hast!"); st.stop()
                        
                        # --- DER FIX: ZUTATEN SKALIEREN BEVOR ABGEZOGEN WIRD ---
                        scaled_recipe_items = []
                        for item in st.session_state.recipe_items:
                            s_item = item.copy()
                            s_item["RezeptMenge"] = s_item["RezeptMenge"] * scaler
                            scaled_recipe_items.append(s_item)
                        
                        # Absicherung: Die skalierte Liste vom Vorrat abziehen
                        save_data(deduct_cooked_recipe_from_inventory(scaled_recipe_items, inv), DB_FILE)
                        saved_g = w_scaled - eat_g
                        
                        # Absicherung Zero Division bei 0g Gesamtgewicht
                        if saved_g > 0 and w_scaled > 0:
                            meal = {"Name": f"Vorbereitet: {r_name}", "Marke": "Selbstgekocht", "Menge": saved_g, "Einheit": "g", "Preis": (cost_scaled/w_scaled)*saved_g, "MHD": get_mhd_default("Selbstgekocht").strftime("%Y-%m-%d")}
                            meal.update(nutris)
                            save_data(add_to_inventory(load_data(DB_FILE).copy(), meal), DB_FILE)
                            
                            if not (lib["Name"] == meal["Name"]).any():
                                lib_e = meal.copy()
                                lib_e.update({"Kategorie": "Selbstgekocht", "Menge_Std": 100, "Einheit_Std": "g"})
                                save_data(pd.concat([lib, pd.DataFrame([lib_e])], ignore_index=True), LIB_FILE)
                        
                        st.success(f"Erfolgreich gekocht! ({scaler} Portionen vom Vorrat abgezogen)")
                        st.session_state.recipe_items = []
                        st.session_state.recipe_phase = "build"
                        st.rerun()
                    else: st.error("Bitte gib dem Gericht einen Namen.")

# ==========================================
# MODUL 3: VORRAT & INVENTUR
# ==========================================
elif menu == "📦 Vorrat & Inventur":
    st.title("📦 Vorratskammer")
    inv_data = load_data(DB_FILE).copy()
    
    if inv_data.empty:
        st.info("🛒 Dein Vorrat ist aktuell leer. Zeit, einkaufen zu gehen!")
    else:
        total_value = pd.to_numeric(inv_data["Preis"], errors='coerce').fillna(0).sum()
        st.metric("Gesamtwert des Vorrats", f"{total_value:.2f} €")
        st.divider()

        inv_data["MHD_Date"] = pd.to_datetime(inv_data["MHD"], errors='coerce')
        critical = inv_data[inv_data["MHD_Date"] <= datetime.now() + timedelta(days=2)]
        if not critical.empty: st.error(f"🔥 **Achtung!** {len(critical)} Produkte laufen in den nächsten 48h ab.")

        search_term = st.text_input("🔍 Vorrat durchsuchen...", placeholder="z.B. Tomaten, Milch, ...")
        filtered_inv = inv_data.copy()
        if search_term:
            filtered_inv = filtered_inv[filtered_inv["Name"].str.contains(search_term, case=False, na=False)]

        tab_view, tab_edit = st.tabs(["👁️ Übersicht", "✏️ Bestand korrigieren"])
        
        with tab_view:
            if filtered_inv.empty:
                st.warning("Kein Produkt mit diesem Namen im Vorrat gefunden.")
            for i, row in filtered_inv.iterrows():
                m_g = to_grams(row["Menge"], row["Einheit"], row["Name"])
                t_color = "#2e7d32" if m_g > 250 else "#fbc02d" if m_g > 0 else "#d32f2f"
                st.markdown(f"<div class='pantry-card' style='border-left: 8px solid {t_color};'><div><span style='font-size: 1.1em; font-weight: bold;'>{row['Name']}</span><br><span style='color: #888;'>MHD: {row['MHD']}</span></div><div style='text-align: right; color: {t_color}; font-weight: bold; font-size: 1.2em;'>{row['Menge']} {row['Einheit']}</div></div>", unsafe_allow_html=True)
        
        with tab_edit:
            st.info("Hier kannst du verdorbene Lebensmittel löschen oder den Bestand manuell anpassen.")
            for i, row in filtered_inv.iterrows():
                col1, col2, col3 = st.columns([3, 1, 1])
                col1.write(f"{row['Name']} ({row['Menge']} {row['Einheit']})")
                new_m = col2.number_input("Neu", value=float(row['Menge']), key=f"edit_{i}", label_visibility="collapsed")
                
                if col3.button("💾", key=f"save_{i}"):
                    orig_idx = row.name # <-- Greift sicher auf den korrekten Index in der Datenbank zu!
                    if new_m <= 0: save_data(delete_inventory_item(inv_data, orig_idx), DB_FILE)
                    else: save_data(update_inventory_item(inv_data, orig_idx, new_m), DB_FILE)
                    st.success("Bestand aktualisiert!"); st.rerun()

# ==========================================
# MODUL 4: STATISTIK DASHBOARD
# ==========================================
elif menu == "📊 Statistik":
    st.title("📊 Finanz & Konsum Dashboard")
    h_data = load_data(HISTORY_FILE).copy()
    s_data = get_stats_data(h_data)
    
    if s_data.empty:
        st.info("📈 Noch keine Ausgaben erfasst. Trage deinen ersten Einkauf ein!")
    else:
        c_year, c_month = st.columns(2)
        year = c_year.selectbox("Jahr", sorted(s_data["Datum"].dt.year.unique(), reverse=True))
        month = c_month.selectbox("Monat (Optional)", ["Alle"] + list(range(1, 13)))
        
        filtered = s_data[s_data["Datum"].dt.year == year]
        if month != "Alle": filtered = filtered[filtered["Datum"].dt.month == month]
        
        st.metric("Gesamtausgaben im Zeitraum", f"{filtered['Preis'].sum():.2f} €")
        fig = px.bar(filtered, x="Datum", y="Preis", color="Aktion", title="Ausgabenverlauf", template="plotly_dark", color_discrete_sequence=px.colors.sequential.Greens_r)
        st.plotly_chart(fig, use_container_width=True)

# ==========================================
# MODUL 5: BIBLIOTHEK
# ==========================================
elif menu == "📚 Bibliothek":
    st.title("📚 Stammdaten-Bibliothek")
    lib_data = load_data(LIB_FILE).copy()
    
    if lib_data.empty:
        st.info("📚 Deine Bibliothek ist leer. Jedes neue Lebensmittel aus dem Wizard landet automatisch hier.")
    else:
        tab_list, tab_edit = st.tabs(["👁️ Übersicht & Löschen", "✏️ Stammdaten bearbeiten"])
        
        with tab_list:
            to_del = st.multiselect("Produkte zum Löschen markieren", lib_data["Name"].tolist())
            if st.button("🗑️ Ausgewählte unwiderruflich löschen") and to_del:
                save_data(lib_data[~lib_data["Name"].isin(to_del)], LIB_FILE)
                st.success("Produkte entfernt."); st.rerun()
            st.dataframe(lib_data, use_container_width=True)
            
        with tab_edit:
            st.info("Tippfehler bei der Aufnahme? Klicke hier doppelt in eine Zelle, um die Makros/Werte direkt zu korrigieren!")
            edited_lib = st.data_editor(lib_data, num_rows="dynamic", use_container_width=True, key="lib_editor")
            if st.button("💾 Änderungen an Stammdaten speichern"):
                save_data(edited_lib, LIB_FILE)
                st.success("Bibliothek erfolgreich aktualisiert!")
                st.rerun()
