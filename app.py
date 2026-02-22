import streamlit as st
import pandas as pd
import json
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from PIL import Image

# --- BACKEND IMPORT ---
from backend import (
    DB_FILE, LIB_FILE, RECIPE_FILE, HISTORY_FILE, NUTRIENTS, ALL_NUTRIENTS, UNITS,
    init_dbs, load_data, save_data, log_history, to_grams, from_grams,
    predict_category, get_mhd_default, fetch_comprehensive_data,
    add_to_inventory, update_inventory_item, delete_inventory_item,
    calculate_recipe_totals, deduct_cooked_recipe_from_inventory, get_stats_data
)

try:
    from pyzbar.pyzbar import decode
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

# ==========================================
# UI SETUP & CSS
# ==========================================
st.set_page_config(page_title="NutriStock Pro", layout="wide", page_icon="🥗")
init_dbs()

st.markdown("""
    <style>
    .stApp { background-color: #0e1117; color: #e0e0e0; }
    .card { background-color: #1e1e1e; padding: 20px; border-radius: 12px; border-left: 5px solid #2e7d32; margin-bottom: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
    .wizard-container { display: flex; justify-content: space-between; background: #161b22; padding: 15px; border-radius: 10px; margin-bottom: 25px; }
    .wizard-step { flex: 1; text-align: center; color: #555; font-weight: bold; border-bottom: 3px solid #333; padding-bottom: 5px; }
    .step-active { color: #2e7d32; border-bottom: 3px solid #2e7d32; }
    .stButton>button { border-radius: 10px; height: 3.2em; font-weight: bold; width: 100%; transition: all 0.3s; }
    .stButton>button:hover { background-color: #1b5e20; border-color: #1b5e20; color: white; }
    .fast-track-box { background-color: #1a202c; padding: 20px; border-radius: 10px; border: 1px solid #2e7d32; margin-bottom: 20px; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# SESSION STATE MANAGEMENT
# ==========================================
if "step" not in st.session_state: st.session_state.step = 1
if "recipe_items" not in st.session_state: st.session_state.recipe_items = []
if "recipe_phase" not in st.session_state: st.session_state.recipe_phase = "build"
if "temp_nutrients" not in st.session_state: st.session_state.temp_nutrients = {n: None for n in ALL_NUTRIENTS}

def clear_aufnahme_session():
    st.session_state.step = 1
    st.session_state.temp_nutrients = {n: None for n in ALL_NUTRIENTS}
    for key in ["t_name", "t_marke", "t_menge", "t_preis", "t_einheit", "t_mhd", "t_kat", "last_barcode"]:
        if key in st.session_state: del st.session_state[key]

# ==========================================
# SIDEBAR NAVIGATION & QUICK DEDUCT
# ==========================================
st.sidebar.title("🩺 NutriStock Pro")
menu = st.sidebar.radio("Menü", ["📥 Lebensmittel Aufnahme", "🍳 Rezept Labor", "📦 Vorrat & Inventur", "📊 Statistik", "📚 Bibliothek"])

st.sidebar.divider()
st.sidebar.subheader("⚡ Quick-Verbrauch")
sidebar_inv = load_data(DB_FILE)
if not sidebar_inv.empty:
    for _, item in sidebar_inv.head(4).iterrows():
        if st.sidebar.button(f"➖ {item['Name']} verbrauchen", key=f"q_{item['Name']}"):
            unit = 1 if item['Einheit'] == "Stk." else 100
            new_inv = deduct_cooked_recipe_from_inventory([{"Name": item['Name'], "RezeptMenge": unit, "Einheit_Std": item['Einheit']}], sidebar_inv)
            save_data(new_inv, DB_FILE)
            st.toast(f"{unit}{item['Einheit']} {item['Name']} abgezogen!")
            st.rerun()

# ==========================================
# MODUL 1: AUFNAHME WIZARD & FAST-TRACK
# ==========================================
if menu == "📥 Lebensmittel Aufnahme":
    st.title("📥 Einkauf eintragen")
    lib = load_data(LIB_FILE)
    inv = load_data(DB_FILE)
    
    # Toggle zwischen Nachkauf und Neuem Produkt
    modus = st.radio("Was möchtest du tun?", ["🔄 Bekanntes Produkt nachkaufen (Fast-Track)", "✨ Neues Produkt aufnehmen (Wizard)"], horizontal=True)
    st.divider()

    # --- WEG A: FAST-TRACK (NACHKAUFEN) ---
    if "Fast-Track" in modus:
        if lib.empty:
            st.info("Deine Bibliothek ist noch leer. Bitte nutze zuerst den Wizard für neue Produkte.")
        else:
            st.markdown("<div class='fast-track-box'>", unsafe_allow_html=True)
            st.subheader("⚡ Blitz-Einlagerung")
            with st.form("fast_track_form"):
                sel_lib = st.selectbox("Welches Produkt hast du gekauft?", lib["Name"].tolist())
                c1, c2, c3 = st.columns(3)
                ft_menge = c1.number_input("Gekaufte Menge*", value=None, step=0.1)
                
                # Hole Standard-Einheit aus Bibliothek
                ref_item = lib[lib["Name"] == sel_lib].iloc[0]
                std_einheit = ref_item["Einheit_Std"]
                ft_einheit = c2.selectbox("Einheit", [std_einheit] + [u for u in UNITS if u != std_einheit])
                
                ft_preis = c3.number_input("Gesamtpreis (€)*", value=None, step=0.01)
                ft_mhd = st.date_input("MHD*", value=get_mhd_default(ref_item["Kategorie"]))
                
                if st.form_submit_button("💾 Sofort Einlagern"):
                    if ft_menge and ft_preis is not None:
                        # Baue Entry aus Bibliotheks-Nährwerten
                        entry = {
                            "Name": sel_lib, "Marke": ref_item["Marke"], 
                            "Menge": ft_menge, "Einheit": ft_einheit, 
                            "Preis": ft_preis, "MHD": ft_mhd.strftime("%Y-%m-%d")
                        }
                        for n in ALL_NUTRIENTS: entry[n] = ref_item[n]
                        
                        save_data(add_to_inventory(inv, entry), DB_FILE)
                        log_history("Aufnahme (Fast-Track)", entry["Name"], entry["Marke"], entry["Menge"], entry["Einheit"], entry["Preis"])
                        st.success(f"{sel_lib} wurde blitzschnell in den Vorrat gelegt!")
                        st.rerun()
                    else: st.error("Bitte Menge und Preis angeben.")
            st.markdown("</div>", unsafe_allow_html=True)

    # --- WEG B: WIZARD (NEUES PRODUKT) ---
    else:
        s1 = "step-active" if st.session_state.step == 1 else ""
        s2 = "step-active" if st.session_state.step == 2 else ""
        s3 = "step-active" if st.session_state.step == 3 else ""
        st.markdown(f"<div class='wizard-container'><div class='wizard-step {s1}'>1. Kaufdaten</div><div class='wizard-step {s2}'>2. Makros</div><div class='wizard-step {s3}'>3. Mikros</div></div>", unsafe_allow_html=True)

        # SCHRITT 1
        if st.session_state.step == 1:
            st.markdown("<div class='card'>", unsafe_allow_html=True)
            c_scan, c_cam = st.columns([2, 1])
            with c_scan: barcode = st.text_input("Barcode", placeholder="Scannen oder tippen...")
            with c_cam:
                cam_img = st.file_uploader("📷 Foto", type=["jpg", "png"], label_visibility="collapsed")
                if cam_img and PYZBAR_AVAILABLE:
                    decoded = decode(Image.open(cam_img).convert('L'))
                    if decoded: barcode = decoded[0].data.decode("utf-8")

            if barcode and barcode != st.session_state.get("last_barcode"):
                with st.spinner("Lade USDA & OFF Daten..."):
                    api_data = fetch_comprehensive_data(barcode, st.secrets["usda_api_key"])
                    st.session_state.t_name, st.session_state.t_marke = api_data["Name"], api_data["Marke"]
                    for n, v in api_data["nutrients"].items(): st.session_state.temp_nutrients[n] = float(v) if v else None
                    st.session_state.last_barcode = barcode
                    st.toast("Nährwerte geladen!", icon="✅")

            with st.form("form_basis"):
                f_name = st.text_input("Name*", value=st.session_state.get("t_name", ""))
                f_marke = st.text_input("Marke", value=st.session_state.get("t_marke", ""))
                c1, c2, c3, c4 = st.columns(4)
                f_menge = c1.number_input("Menge*", value=None, placeholder="Zahl...", step=0.1)
                f_einheit = c2.selectbox("Einheit*", UNITS)
                f_preis = c3.number_input("Preis (€)*", value=None, placeholder="0.00", step=0.01)
                cat_sugg = predict_category(f_name)
                f_mhd = c4.date_input("MHD*", value=get_mhd_default(cat_sugg))
                
                if st.form_submit_button("Weiter zu Makros ➡️"):
                    if f_name and f_menge is not None and f_preis is not None:
                        st.session_state.t_name, st.session_state.t_marke, st.session_state.t_menge = f_name, f_marke, f_menge
                        st.session_state.t_einheit, st.session_state.t_preis, st.session_state.t_mhd = f_einheit, f_preis, f_mhd
                        st.session_state.t_kat = cat_sugg
                        st.session_state.step = 2
                        st.rerun()
                    else: st.error("Bitte alle mit * markierten Felder ausfüllen.")
            st.markdown("</div>", unsafe_allow_html=True)

        # SCHRITT 2
        elif st.session_state.step == 2:
            st.subheader(f"🍎 Makros für {st.session_state.t_name}")
            with st.form("form_makro"):
                cols = st.columns(5)
                for i, m in enumerate(NUTRIENTS["Makronährstoffe"]):
                    st.session_state.temp_nutrients[m] = cols[i].number_input(m.replace("_100", ""), value=st.session_state.temp_nutrients.get(m), placeholder="0.0")
                cb, cs, cn = st.columns([1, 2, 2])
                if cb.form_submit_button("⬅️ Zurück"): st.session_state.step = 1; st.rerun()
                if cs.form_submit_button("💾 Direkt Speichern"): st.session_state.do_save = True
                if cn.form_submit_button("🔬 Mikros bearbeiten ➡️"): st.session_state.step = 3; st.rerun()

        # SCHRITT 3
        elif st.session_state.step == 3:
            st.subheader("🔬 Mikronährstoffe (pro 100g)")
            with st.form("form_mikro"):
                for g_name, items in NUTRIENTS.items():
                    if g_name == "Makronährstoffe": continue
                    st.markdown(f"**{g_name}**")
                    mcols = st.columns(4)
                    for i, item in enumerate(items):
                        st.session_state.temp_nutrients[item] = mcols[i%4].number_input(item, value=st.session_state.temp_nutrients.get(item), placeholder="0.0")
                if st.form_submit_button("✅ Final Speichern"): st.session_state.do_save = True; st.rerun()

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
    lib, inv = load_data(LIB_FILE), load_data(DB_FILE)

    # PHASE 1: BUILD
    if st.session_state.recipe_phase == "build":
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        c_sel, c_qty, c_add = st.columns([3, 1, 1])
        sel_item = c_sel.selectbox("Zutat aus Bibliothek", ["--"] + lib["Name"].tolist())
        qty_item = c_qty.number_input("Menge", value=None, placeholder="z.B. 150")
        if c_add.button("➕ Hinzufügen"):
            if sel_item != "--" and qty_item is not None:
                details = lib[lib["Name"] == sel_item].iloc[0].to_dict()
                details["RezeptMenge"] = float(qty_item)
                st.session_state.recipe_items.append(details)
                st.toast(f"{sel_item} hinzugefügt!")
        st.markdown("</div>", unsafe_allow_html=True)

        if st.session_state.recipe_items:
            st.subheader("📋 Meine Zutaten")
            for i, item in enumerate(st.session_state.recipe_items):
                colz = st.columns([4, 1])
                colz[0].markdown(f"**{item['RezeptMenge']} {item['Einheit_Std']}** {item['Name']}")
                if colz[1].button("🗑️", key=f"del_{i}"): st.session_state.recipe_items.pop(i); st.rerun()
            
            st.divider()
            c_check, c_finish = st.columns(2)
            
            # Einkaufslisten-Feature
            if c_check.button("🛒 Einkaufsliste prüfen"):
                missing = deduct_cooked_recipe_from_inventory(st.session_state.recipe_items, inv, generate_shopping_list=True)
                if missing:
                    st.warning("⚠️ Folgende Zutaten fehlen im Vorrat:")
                    st.dataframe(pd.DataFrame(missing))
                else: st.success("✅ Alle Zutaten sind im Vorrat ausreichend vorhanden!")

            if c_finish.button("🏁 Rezept fertigstellen (Weiter)"):
                st.session_state.recipe_phase = "summary"
                st.rerun()

    # PHASE 2: SUMMARY & KOCHEN
    elif st.session_state.recipe_phase == "summary":
        st.subheader("📊 Zusammenfassung & Speichern")
        
        scaler = st.slider("Personen/Portionen anpassen", 0.5, 5.0, 1.0, 0.5)
        w, cost, nutris = calculate_recipe_totals(st.session_state.recipe_items)
        w, cost = w * scaler, cost * scaler

        r_keys = NUTRIENTS["Mineralstoffe"][:8]
        fig = go.Figure(data=go.Scatterpolar(r=[nutris.get(k, 0) for k in r_keys], theta=r_keys, fill='toself', line_color='#2e7d32'))
        fig.update_layout(polar=dict(radialaxis=dict(visible=False)), showlegend=False, height=350, margin=dict(t=20, b=20), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        
        c_chart, c_data = st.columns([1, 1])
        c_chart.plotly_chart(fig, use_container_width=True)
        c_data.markdown(f"<div class='card'><b>Gewicht gesamt:</b> {w:.0f}g<br><b>Kosten gesamt:</b> {cost:.2f}€<br><b>Kalorien (pro 100g):</b> {nutris['kcal_100']:.0f} kcal</div>", unsafe_allow_html=True)

        with st.form("recipe_finish_form"):
            r_name = st.text_input("Name für Mealprep*", placeholder="z.B. Linsen-Dal")
            eat_now = st.number_input("Wie viel isst du jetzt direkt? (in g)", value=None, placeholder="0.0")
            
            c_back, c_save = st.columns(2)
            if c_back.form_submit_button("⬅️ Zurück zum Bearbeiten"):
                st.session_state.recipe_phase = "build"
                st.rerun()
            
            if c_save.form_submit_button("🚀 Kochen & Mealprep anlegen"):
                if r_name:
                    eat_g = float(eat_now) if eat_now else 0.0
                    if eat_g > w: st.error("Du kannst nicht mehr essen, als du gekocht hast!"); st.stop()
                    
                    save_data(deduct_cooked_recipe_from_inventory(st.session_state.recipe_items, inv), DB_FILE)
                    
                    saved_g = w - eat_g
                    if saved_g > 0:
                        meal = {"Name": f"Vorbereitet: {r_name}", "Marke": "Selbstgekocht", "Menge": saved_g, "Einheit": "g", "Preis": (cost/w)*saved_g, "MHD": get_mhd_default("Selbstgekocht").strftime("%Y-%m-%d")}
                        meal.update(nutris)
                        save_data(add_to_inventory(load_data(DB_FILE), meal), DB_FILE)
                        if not (lib["Name"] == meal["Name"]).any():
                            lib_e = meal.copy(); lib_e.update({"Kategorie": "Selbstgekocht", "Menge_Std": 100, "Einheit_Std": "g"})
                            save_data(pd.concat([lib, pd.DataFrame([lib_e])], ignore_index=True), LIB_FILE)
                    
                    st.success("Rezept erfolgreich gekocht und Bestände abgezogen!")
                    st.session_state.recipe_items = []; st.session_state.recipe_phase = "build"
                    st.rerun()
                else: st.error("Bitte gib dem Gericht einen Namen.")

# ==========================================
# MODUL 3: VORRAT & INVENTUR
# ==========================================
elif menu == "📦 Vorrat & Inventur":
    st.title("📦 Vorratskammer")
    inv_data = load_data(DB_FILE)
    
    if not inv_data.empty:
        inv_data["MHD_Date"] = pd.to_datetime(inv_data["MHD"], errors='coerce')
        critical = inv_data[inv_data["MHD_Date"] <= datetime.now() + timedelta(days=2)]
        if not critical.empty:
            st.error(f"🔥 **Achtung!** {len(critical)} Produkte laufen in den nächsten 48h ab (z.B. {critical.iloc[0]['Name']}).")

        tab_view, tab_edit = st.tabs(["👁️ Übersicht", "✏️ Bestand korrigieren"])
        
        with tab_view:
            for i, row in inv_data.iterrows():
                m_g = to_grams(row["Menge"], row["Einheit"], row["Name"])
                t_color = "#2e7d32" if m_g > 250 else "#fbc02d" if m_g > 0 else "#d32f2f"
                st.markdown(f"<div class='card' style='border-left: 8px solid {t_color};'><div style='display:flex; justify-content:space-between;'><b>{row['Name']}</b><span style='color:{t_color}; font-weight:bold;'>{row['Menge']} {row['Einheit']}</span></div><small>MHD: {row['MHD']}</small></div>", unsafe_allow_html=True)
        
        with tab_edit:
            st.info("Hier kannst du verdorbene oder manuell verbrauchte Lebensmittel korrigieren (Preis passt sich automatisch an).")
            for i, row in inv_data.iterrows():
                col1, col2, col3 = st.columns([3, 1, 1])
                col1.write(f"{row['Name']} ({row['Menge']} {row['Einheit']})")
                new_m = col2.number_input("Neu", value=float(row['Menge']), key=f"edit_{i}", label_visibility="collapsed")
                if col3.button("💾", key=f"save_{i}"):
                    if new_m <= 0: save_data(delete_inventory_item(inv_data, i), DB_FILE)
                    else: save_data(update_inventory_item(inv_data, i, new_m), DB_FILE)
                    st.success("Bestand aktualisiert!"); st.rerun()

# ==========================================
# MODUL 4: STATISTIK DASHBOARD
# ==========================================
elif menu == "📊 Statistik":
    st.title("📊 Finanz & Konsum Dashboard")
    h_data = load_data(HISTORY_FILE)
    s_data = get_stats_data(h_data)
    
    if not s_data.empty:
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
    lib_data = load_data(LIB_FILE)
    if not lib_data.empty:
        to_del = st.multiselect("Produkte zum Löschen markieren", lib_data["Name"].tolist())
        if st.button("🗑️ Ausgewählte unwiderruflich löschen") and to_del:
            save_data(lib_data[~lib_data["Name"].isin(to_del)], LIB_FILE)
            st.success("Produkte entfernt."); st.rerun()
        st.dataframe(lib_data, use_container_width=True)
