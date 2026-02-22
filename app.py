import streamlit as st
import pandas as pd
import json
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from PIL import Image

# --- BACKEND IMPORT ---
# Wir importieren alle Logik-Funktionen aus deiner backend.py
from backend import (
    DB_FILE, LIB_FILE, RECIPE_FILE, HISTORY_FILE, NUTRIENTS, ALL_NUTRIENTS, UNITS,
    init_dbs, load_data, save_data, log_history, to_grams, from_grams,
    predict_category, get_mhd_default, fetch_comprehensive_data,
    add_to_inventory, calculate_recipe_totals, deduct_cooked_recipe_from_inventory,
    is_fuzzy_match, get_stats_data
)

# --- SCANNER CHECK ---
try:
    from pyzbar.pyzbar import decode
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

# --- UI SETUP ---
st.set_page_config(page_title="NutriStock Pro", layout="wide", page_icon="🥗")
init_dbs()

# --- CSS INJECTION (Vorschlag 2, 9, 10: Modern Dark Card UI) ---
st.markdown("""
    <style>
    /* Globales Styling */
    .stApp { background-color: #0e1117; color: #e0e0e0; }
    .stHeader { background-color: transparent; }
    
    /* Karten-Design für Produkte und Rezepte */
    .card {
        background-color: #1e1e1e;
        padding: 20px;
        border-radius: 15px;
        border-left: 6px solid #2e7d32;
        margin-bottom: 15px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.4);
    }
    
    /* Wizard-Fortschrittsanzeige */
    .wizard-header {
        display: flex;
        justify-content: space-between;
        margin-bottom: 30px;
        background: #161b22;
        padding: 15px;
        border-radius: 12px;
    }
    .wizard-step {
        flex: 1;
        text-align: center;
        padding: 10px;
        font-weight: bold;
        color: #444;
        border-bottom: 3px solid #333;
    }
    .active-step {
        color: #2e7d32;
        border-bottom: 3px solid #2e7d32;
    }
    
    /* Buttons */
    .stButton>button {
        border-radius: 12px;
        height: 3.5em;
        font-weight: bold;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(46, 125, 50, 0.3);
    }
    </style>
    """, unsafe_allow_html=True)

# --- SESSION STATE INITIALISIERUNG (Vorschlag 3: Smart Clear) ---
if "step" not in st.session_state: st.session_state.step = 1
if "recipe_items" not in st.session_state: st.session_state.recipe_items = []
if "temp_nutrients" not in st.session_state: st.session_state.temp_nutrients = {n: None for n in ALL_NUTRIENTS}
if "last_barcode" not in st.session_state: st.session_state.last_barcode = ""

def clear_entry_session():
    """Löscht alle Puffer nach dem Speichern (Vorschlag 3)."""
    st.session_state.step = 1
    st.session_state.temp_nutrients = {n: None for n in ALL_NUTRIENTS}
    for key in ["t_name", "t_marke", "t_menge", "t_preis", "t_einheit", "t_mhd", "t_kat"]:
        if key in st.session_state: del st.session_state[key]
    st.session_state.last_barcode = ""

# --- SIDEBAR NAVIGATION ---
st.sidebar.title("🩺 NutriStock Pro")
menu = st.sidebar.radio("Navigation", ["📥 Aufnahme Wizard", "🍳 Rezept Labor", "📦 Vorrat", "📊 Statistik", "📚 Bibliothek"])

# Quick-Deduct Dashboard (Vorschlag 29)
st.sidebar.divider()
st.sidebar.subheader("⚡ Quick-Abzug (100g/Stk)")
sidebar_inv = load_data(DB_FILE)
if not sidebar_inv.empty:
    for _, item in sidebar_inv.head(3).iterrows():
        if st.sidebar.button(f"➖ {item['Name']}", key=f"quick_{item['Name']}"):
            unit = 1 if item['Einheit'] == "Stk." else 100
            new_inv = deduct_cooked_recipe_from_inventory([{"Name": item['Name'], "RezeptMenge": unit, "Einheit_Std": item['Einheit']}], sidebar_inv)
            save_data(new_inv, DB_FILE)
            st.toast(f"{item['Name']} reduziert!")
            st.rerun()

# ==========================================
# MODUL: AUFNAHME WIZARD (Vorschlag 1, 4, 5)
# ==========================================
if menu == "📥 Aufnahme Wizard":
    st.title("📥 Neues Lebensmittel aufnehmen")
    
    # Wizard Progress Bar
    s1, s2, s3 = ("active-step", "", "") if st.session_state.step == 1 else (("", "active-step", "") if st.session_state.step == 2 else ("", "", "active-step"))
    st.markdown(f"""
        <div class='wizard-header'>
            <div class='wizard-step {s1}'>1. Basis & Preis</div>
            <div class='wizard-step {s2}'>2. Makros</div>
            <div class='wizard-step {s3}'>3. Mikros</div>
        </div>
    """, unsafe_allow_html=True)

    # --- STUFE 1: BASISDATEN & SCAN ---
    if st.session_state.step == 1:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        col_scan, col_manual = st.columns([1, 1])
        
        with col_scan:
            barcode = st.text_input("Barcode scannen/tippen", placeholder="Warte auf Eingabe...")
            cam_img = st.file_uploader("📷 Oder Barcode-Foto hochladen", type=["jpg", "png"])
            if cam_img and PYZBAR_AVAILABLE:
                decoded = decode(Image.open(cam_img).convert('L'))
                if decoded: 
                    barcode = decoded[0].data.decode("utf-8")
                    st.success(f"Barcode erkannt: {barcode}")

        if barcode and barcode != st.session_state.last_barcode:
            with st.spinner("Synchronisiere API-Daten..."):
                api_data = fetch_comprehensive_data(barcode, st.secrets["usda_api_key"])
                st.session_state.t_name = api_data["Name"]
                st.session_state.t_marke = api_data["Marke"]
                for n, v in api_data["nutrients"].items():
                    st.session_state.temp_nutrients[n] = float(v) if v else None
                st.session_state.last_barcode = barcode
                st.toast("Daten von OFF/USDA geladen!")

        with st.form("basis_form"):
            f_name = st.text_input("Produkt Name*", value=st.session_state.get("t_name", ""))
            f_marke = st.text_input("Marke", value=st.session_state.get("t_marke", ""))
            
            c1, c2, c3, c4 = st.columns(4)
            f_menge = c1.number_input("Menge*", value=None, placeholder="Zahl...", step=0.1)
            f_einheit = c2.selectbox("Einheit", UNITS)
            f_preis = c3.number_input("Gesamtpreis (€)*", value=None, placeholder="0.00", step=0.01)
            
            cat = predict_category(f_name)
            f_mhd = c4.date_input("MHD", value=get_mhd_default(cat))
            
            if st.form_submit_button("Weiter zu Makros ➡️"):
                if f_name and f_menge and f_preis is not None:
                    st.session_state.t_name, st.session_state.t_marke = f_name, f_marke
                    st.session_state.t_menge, st.session_state.t_einheit = f_menge, f_einheit
                    st.session_state.t_preis, st.session_state.t_mhd = f_preis, f_mhd
                    st.session_state.t_kat = cat
                    st.session_state.step = 2
                    st.rerun()
                else: st.error("Bitte alle Pflichtfelder (*) ausfüllen.")
        st.markdown("</div>", unsafe_allow_html=True)

    # --- STUFE 2: MAKROS ---
    elif st.session_state.step == 2:
        st.subheader(f"🍎 Makronährstoffe (pro 100g) für {st.session_state.t_name}")
        with st.form("makro_form"):
            m_cols = st.columns(5)
            for i, m in enumerate(NUTRIENTS["Makronährstoffe"]):
                st.session_state.temp_nutrients[m] = m_cols[i].number_input(
                    m.replace("_100", ""), 
                    value=st.session_state.temp_nutrients.get(m),
                    placeholder="Eintragen..."
                )
            
            c_back, c_save, c_next = st.columns([1, 2, 2])
            if c_back.form_submit_button("⬅️ Zurück"):
                st.session_state.step = 1
                st.rerun()
            if c_save.form_submit_button("💾 Direkt Speichern"):
                st.session_state.final_save = True
            if c_next.form_submit_button("🔬 Mikros bearbeiten ➡️"):
                st.session_state.step = 3
                st.rerun()

    # --- STUFE 3: MIKROS ---
    elif st.session_state.step == 3:
        st.subheader("🔬 Mikronährstoffe (pro 100g)")
        with st.form("mikro_form"):
            for group, items in NUTRIENTS.items():
                if group == "Makronährstoffe": continue
                st.markdown(f"**{group}**")
                cols = st.columns(4)
                for i, item in enumerate(items):
                    st.session_state.temp_nutrients[item] = cols[i%4].number_input(
                        item, value=st.session_state.temp_nutrients.get(item), placeholder="0.0"
                    )
            
            if st.form_submit_button("✅ Aufnahme abschließen & Speichern"):
                st.session_state.final_save = True
                st.rerun()

    # --- SPEICHER-PROZESS ---
    if st.session_state.get("final_save"):
        inv, lib = load_data(DB_FILE), load_data(LIB_FILE)
        new_entry = {
            "Name": st.session_state.t_name, "Marke": st.session_state.t_marke,
            "Menge": st.session_state.t_menge, "Einheit": st.session_state.t_einheit,
            "Preis": st.session_state.t_preis, "MHD": st.session_state.t_mhd.strftime("%Y-%m-%d")
        }
        for n in ALL_NUTRIENTS:
            new_entry[n] = float(st.session_state.temp_nutrients.get(n) or 0.0)
        
        # Batch-Save in Vorrat
        save_data(add_to_inventory(inv, new_entry), DB_FILE)
        
        # Stammdaten-Update (Bibliothek)
        if not lib[lib["Name"] == new_entry["Name"]].any().any():
            lib_entry = new_entry.copy()
            lib_entry.update({
                "Kategorie": st.session_state.t_kat, 
                "Menge_Std": 100.0, 
                "Einheit_Std": new_entry["Einheit"] if new_entry["Einheit"] != "Stk." else "Stk."
            })
            save_data(pd.concat([lib, pd.DataFrame([lib_entry])], ignore_index=True), LIB_FILE)
        
        log_history("Aufnahme", new_entry["Name"], new_entry["Marke"], new_entry["Menge"], new_entry["Einheit"], new_entry["Preis"])
        st.success("Erfolgreich eingelagert!")
        clear_entry_session()
        st.rerun()

# ==========================================
# MODUL: REZEPT LABOR (Vorschlag 6, 7, 10, 14)
# ==========================================
elif menu == "🍳 Rezept Labor":
    st.title("🍳 Rezept-Labor & Mealprep")
    lib = load_data(LIB_FILE)
    inv = load_data(DB_FILE)

    # 1. Zutat hinzufügen
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    c_sel, c_qty, c_add = st.columns([3, 1, 1])
    sel_item = c_sel.selectbox("Zutat aus Bibliothek wählen", ["--"] + lib["Name"].tolist())
    qty_item = c_qty.number_input("Menge", value=None, placeholder="Menge...")
    
    if c_add.button("➕ Zutat hinzufügen"):
        if sel_item != "--" and qty_item:
            details = lib[lib["Name"] == sel_item].iloc[0].to_dict()
            details["RezeptMenge"] = qty_item
            st.session_state.recipe_items.append(details)
            st.toast(f"{sel_item} hinzugefügt!")
    st.markdown("</div>", unsafe_allow_html=True)

    # 2. Aktuelle Zutatenliste mit Löschfunktion
    if st.session_state.recipe_items:
        st.subheader("📋 Aktuelle Zutaten")
        for i, item in enumerate(st.session_state.recipe_items):
            colz = st.columns([4, 1, 1])
            colz[0].markdown(f"**{item['RezeptMenge']} {item['Einheit_Std']}** {item['Name']} ({item['Marke']})")
            if colz[2].button("🗑️", key=f"del_{i}"):
                st.session_state.recipe_items.pop(i)
                st.rerun()
        
        # 3. Skalierung & Live-Berechnung (Vorschlag 15)
        scaler = st.slider("Portionen-Skalierung", 0.5, 5.0, 1.0, 0.5)
        w, cost, nutris = calculate_recipe_totals(st.session_state.recipe_items)
        w, cost = w * scaler, cost * scaler

        # Floating Info-Bar (Vorschlag 10)
        st.markdown(f"""
            <div style='background: #161b22; padding: 20px; border-radius: 12px; border-bottom: 5px solid #2e7d32; position: sticky; bottom: 0;'>
                <h4 style='margin:0;'>Live-Analyse für {scaler} Portionen:</h4>
                <b>Gewicht:</b> {w:.0f}g | <b>Kosten:</b> {cost:.2f}€ | <b>Kalorien (100g):</b> {nutris['kcal_100']:.1f} kcal
            </div>
        """, unsafe_allow_html=True)

        # Radar Chart für Nährstoffe (Vorschlag 14)
        
        radar_keys = NUTRIENTS["Mineralstoffe"][:8]
        fig = go.Figure(data=go.Scatterpolar(
            r=[nutris.get(k, 0) for k in radar_keys],
            theta=radar_keys,
            fill='toself',
            line_color='#2e7d32'
        ))
        fig.update_layout(polar=dict(radialaxis=dict(visible=False)), showlegend=False, height=400, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

        # 4. Finalisieren (Vorschlag 7, 21)
        with st.form("recipe_finish"):
            rec_name = st.text_input("Name des Rezepts*", placeholder="z.B. Mein Hummus")
            eat_g = st.number_input("Jetzt essen (in g)", value=0.0)
            if st.form_submit_button("🚀 Rezept kochen & Rest einlagern"):
                if rec_name:
                    # Bestände abbuchen
                    save_data(deduct_cooked_recipe_from_inventory(st.session_state.recipe_items, inv), DB_FILE)
                    
                    # Mealprep Logik
                    saved_g = w - eat_g
                    if saved_g > 0:
                        meal = {
                            "Name": f"Vorbereitet: {rec_name}", "Marke": "Küche",
                            "Menge": saved_g, "Einheit": "g", "Preis": (cost/w)*saved_g,
                            "MHD": get_mhd_default("Selbstgekocht").strftime("%Y-%m-%d")
                        }
                        meal.update(nutris)
                        save_data(add_to_inventory(load_data(DB_FILE), meal), DB_FILE)
                        
                        # In Bibliothek für spätere Nutzung
                        if not (lib["Name"] == meal["Name"]).any():
                            lib_e = meal.copy()
                            lib_e.update({"Kategorie": "Selbstgekocht", "Menge_Std": 100, "Einheit_Std": "g"})
                            save_data(pd.concat([lib, pd.DataFrame([lib_e])], ignore_index=True), LIB_FILE)
                    
                    st.success(f"{rec_name} wurde verarbeitet!")
                    st.session_state.recipe_items = []
                    st.rerun()

# ==========================================
# MODUL: VORRAT (Traffic Light UI, Vorschlag 7, 22)
# ==========================================
elif menu == "📦 Vorrat":
    st.title("📦 Vorratskammer")
    inv_data = load_data(DB_FILE)
    
    if not inv_data.empty:
        # Zero Waste Check (Vorschlag 21)
        critical_mhd = inv_data[pd.to_datetime(inv_data["MHD"]) <= datetime.now() + timedelta(days=2)]
        if not critical_mhd.empty:
            st.error(f"🔥 **Zero-Waste-Alarm:** {len(critical_mhd)} Produkte laufen bald ab!")

        # Grid-Ansicht
        for i, row in inv_data.iterrows():
            m_g = to_grams(row["Menge"], row["Einheit"], row["Name"])
            # Ampel-Farbe basierend auf Menge
            t_color = "#2e7d32" if m_g > 250 else "#fbc02d" if m_g > 0 else "#d32f2f"
            
            st.markdown(f"""
                <div class='card' style='border-left: 10px solid {t_color};'>
                    <div style='display:flex; justify-content:space-between;'>
                        <b>{row['Name']}</b>
                        <span style='color:{t_color}; font-weight:bold;'>{row['Menge']} {row['Einheit']}</span>
                    </div>
                    <small>{row['Marke']} | MHD: {row['MHD']}</small>
                </div>
            """, unsafe_allow_html=True)

# ==========================================
# MODUL: STATISTIK (Vorschlag 8, 25)
# ==========================================
elif menu == "📊 Statistik":
    st.title("📊 Finanz-Dashboard")
    h_data = load_data(HISTORY_FILE)
    s_data = get_stats_data(h_data)
    
    if not s_data.empty:
        c_yr, c_mo = st.columns(2)
        year = c_yr.selectbox("Jahr wählen", sorted(s_data["Datum"].dt.year.unique(), reverse=True))
        month = c_mo.selectbox("Monat wählen (Optional)", ["Alle"] + list(range(1, 13)))
        
        filtered = s_data[s_data["Datum"].dt.year == year]
        if month != "Alle": filtered = filtered[filtered["Datum"].dt.month == month]
        
        # Metriken
        st.metric("Gesamtausgaben", f"{filtered['Preis'].sum():.2f} €")
        
        # Visualisierung
        fig_stats = px.bar(filtered, x="Datum", y="Preis", color="Aktion", title="Ausgaben-Verlauf", template="plotly_dark")
        st.plotly_chart(fig_stats, use_container_width=True)

# ==========================================
# MODUL: BIBLIOTHEK (Bulk Action, Vorschlag 24)
# ==========================================
elif menu == "📚 Bibliothek":
    st.title("📚 Stammdaten-Bibliothek")
    lib_data = load_data(LIB_FILE)
    
    if not lib_data.empty:
        to_del = st.multiselect("Produkte zum Löschen markieren", lib_data["Name"].tolist())
        if st.button("🗑️ Markierte Produkte unwiderruflich löschen") and to_del:
            new_lib = lib_data[~lib_data["Name"].isin(to_del)]
            save_data(new_lib, LIB_FILE)
            st.success("Bibliothek bereinigt!")
            st.rerun()
            
        st.dataframe(lib_data, use_container_width=True)
