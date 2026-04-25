import streamlit as st
import pandas as pd
import json
import hashlib
from supabase import create_client, Client
from datetime import datetime
import plotly.graph_objects as go

st.set_page_config(
    page_title="UNIVISA — Dashboard de Receitas",
    page_icon="🟠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── SUPABASE ────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = get_supabase()

MESES    = ['JANEIRO','FEVEREIRO','MARÇO','ABRIL','MAIO','JUNHO',
            'JULHO','AGOSTO','SETEMBRO','OUTUBRO','NOVEMBRO','DEZEMBRO']
MESES_SH = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']

def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()
def fmt_brl(v):
    if not v or v == 0: return "—"
    return f"R$ {v:,.2f}".replace(",","X").replace(".",",").replace("X",".")
def fmt_short(v):
    if not v or v == 0: return "—"
    if v >= 1e6: return f"R${v/1e6:.1f}M"
    if v >= 1e3: return f"R${v/1e3:.0f}K"
    return fmt_brl(v)

# ─── DB FUNCTIONS ────────────────────────────────────────────────────────────
def do_login(login_str, senha):
    try:
        res = supabase.table("users").select("*")\
            .eq("login", login_str.lower().strip())\
            .eq("senha_hash", hash_senha(senha)).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        st.error(f"Erro: {e}")
    return None

@st.cache_data(ttl=30)
def get_users():
    try: return supabase.table("users").select("id,login,nome,role").execute().data or []
    except: return []

def add_user(login_str, senha, nome, role="user"):
    try:
        supabase.table("users").insert({
            "login": login_str.lower().strip(),
            "senha_hash": hash_senha(senha),
            "nome": nome or login_str, "role": role
        }).execute()
        get_users.clear(); return True
    except Exception as e:
        st.error(str(e)); return False

def delete_user(uid):
    try:
        supabase.table("users").delete().eq("id", uid).execute()
        get_users.clear(); return True
    except: return False

@st.cache_data(ttl=60)
def get_uploads():
    try:
        return supabase.table("uploads")\
            .select("id,nome_arquivo,ano,criado_em")\
            .order("criado_em", desc=True).execute().data or []
    except: return []

def save_upload(nome, ano, dados, uid):
    try:
        res = supabase.table("uploads").insert({
            "nome_arquivo": nome, "ano": ano,
            "dados": json.dumps(dados), "usuario_id": uid,
            "criado_em": datetime.now().isoformat()
        }).execute()
        get_uploads.clear()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        st.error(f"Erro: {e}"); return None

def load_upload(upload_id):
    try:
        res = supabase.table("uploads").select("dados,nome_arquivo,ano").eq("id", upload_id).execute()
        if res.data:
            r = res.data[0]
            return json.loads(r["dados"]), r["nome_arquivo"], r["ano"]
    except Exception as e:
        st.error(f"Erro: {e}")
    return None, None, None

def delete_upload(upload_id):
    try:
        supabase.table("uploads").delete().eq("id", upload_id).execute()
        get_uploads.clear(); return True
    except: return False

# ─── PARSER ──────────────────────────────────────────────────────────────────
def parse_value(v):
    if v is None or v == "" or v == 0: return 0.0
    if isinstance(v, (int, float)): return abs(float(v))
    import re
    s = re.sub(r"[a-zA-Z\s]","",str(v).strip())
    if not s: return 0.0
    if re.match(r"^\d{1,3}(\.\d{3})+,\d+$", s):
        return abs(float(s.replace(".","").replace(",",".")))
    return abs(float(s.replace(",",".")) or 0)

@st.cache_data
def parse_sheet(raw_tuple):
    raw = [list(r) for r in raw_tuple]
    L0 = {"RECEITA","TAXAS","GRADUAÇÃO","PÓS-GRADUAÇÃO","CAMB"}
    AREA_PARTS = ["CIÊNCIAS EXATAS","CIÊNCIAS HUMANAS","CIÊNCIAS LINGUÍSTICAS",
                  "CIÊNCIAS SOCIAIS","CIÊNCIAS DA SAÚDE","CIÊNCIA DA SAÚDE","CIÊNCIAS TECNOLOGIAS"]
    hRow = next((i for i,row in enumerate(raw)
                 if any(str(c or "").strip().upper()=="JANEIRO" for c in row)), -1)
    if hRow < 0: return [], None
    hdrs = [str(c or "").strip().upper() for c in raw[hRow]]
    mIdx = {m: hdrs.index(m) for m in MESES if m in hdrs}
    totCol = hdrs.index("TOTAL") if "TOTAL" in hdrs else -1
    import re
    ano = None
    try:
        t = str((raw[1] or [None,None])[1] or "")
        m = re.search(r"20\d\d", t)
        if m: ano = m.group(0)
    except: pass
    rows, cat, area = [], "", ""
    for row in raw[hRow+1:]:
        if not row: continue
        raw_name = row[1] if len(row)>1 else None
        if raw_name is None: continue
        name = str(raw_name).strip()
        if not name: continue
        nu = name.upper().strip()
        nivel = 2
        if nu in L0 or nu.split(" ")[0] in L0: nivel, cat, area = 0, name, ""
        elif any(p[:14] in nu for p in AREA_PARTS): nivel, area = 1, name
        meses = {m: parse_value(row[mIdx[m]]) if mIdx.get(m) is not None and mIdx[m]<len(row) else 0.0 for m in MESES}
        total = 0.0
        if totCol >= 0 and totCol < len(row): total = parse_value(row[totCol])
        if not total and len(row)>17: total = parse_value(row[17])
        if not total: total = sum(meses.values())
        rows.append({"nome":name,"nivel":nivel,"categoria":cat,"area":area,"meses":meses,"total":total})
    return rows, ano

# ─── SESSION ─────────────────────────────────────────────────────────────────
for k, v in [("user",None),("dados",[]),("ano","2025"),
             ("arquivo",None),("dark_mode",False),("aba","dashboard"),("sb_aba","planilhas")]:
    if k not in st.session_state:
        st.session_state[k] = v

dark = st.session_state.dark_mode
BG      = "#0d0500"   if dark else "#FFFFFF"
BG2     = "#1a0a00"   if dark else "#FFFFFF"
BG3     = "#2A0E00"   if dark else "#FFF8F4"
BORDER  = "#7A3500"   if dark else "#FFD5B8"
TEXT    = "#F0F0F0"   if dark else "#111111"
TEXT2   = "#FF8C42"   if dark else "#C84E00"
CARD    = "#1a0a00"   if dark else "#FFFFFF"
INP_BG  = "#2A0E00"   if dark else "#FFF8F4"
INP_BR  = "#7A3500"   if dark else "#FFB380"
CHART   = "#1a0a00"   if dark else "#FFFFFF"
GRID    = "#5A2000"   if dark else "#FFE5D0"

# ─── CSS ─────────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [class*="css"], .stApp {{
  font-family: 'Sora', sans-serif !important;
  background-color: {BG} !important;
  color: {TEXT} !important;
}}
.block-container {{ padding: 0 1.2rem 2rem !important; max-width: 100% !important; }}

/* ── Esconde header vermelho do Streamlit ── */
header[data-testid="stHeader"] {{ display: none !important; }}
#MainMenu {{ visibility: hidden !important; }}
footer {{ display: none !important; }}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {{
  background: linear-gradient(180deg, #1a0a00 0%, #0d0500 100%) !important;
  border-right: 2.5px solid #F26522 !important;
  padding-top: 0 !important;
}}
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] div {{ color: #FFD5B8 !important; }}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {{ color: white !important; }}
section[data-testid="stSidebar"] input {{
  background: #2A0E00 !important; color: white !important;
  border-color: #7A3500 !important;
}}
section[data-testid="stSidebar"] .stButton > button {{
  background: rgba(242,101,34,.15) !important;
  border: 1px solid #F26522 !important;
  color: #FF8C42 !important;
  border-radius: 8px !important; font-weight: 600 !important;
  transition: all .2s !important;
}}
section[data-testid="stSidebar"] .stButton > button:hover {{
  background: #F26522 !important; color: white !important;
}}
/* Botão de colapsar sidebar */
div[data-testid="collapsedControl"] {{
  background: #F26522 !important;
  border-radius: 0 10px 10px 0 !important;
  top: 50% !important;
}}
div[data-testid="collapsedControl"] svg {{ fill: white !important; }}
button[data-testid="baseButton-headerNoPadding"] {{
  background: #F26522 !important; color: white !important;
}}

/* ── Inputs ── */
div[data-baseweb="input"] input,
div[data-baseweb="select"] div,
div[data-baseweb="textarea"] textarea {{
  background: {INP_BG} !important;
  color: {TEXT} !important;
  border-color: {INP_BR} !important;
}}

/* ── Botões gerais ── */
.stButton > button {{
  background: rgba(242,101,34,.12) !important;
  color: #F26522 !important;
  border: 1.5px solid #F26522 !important;
  border-radius: 8px !important;
  font-family: 'Sora', sans-serif !important;
  font-weight: 600 !important;
  transition: all .2s !important;
}}
.stButton > button:hover {{
  background: #F26522 !important;
  color: white !important;
}}

/* ── Expander ── */
details {{
  background: {BG3} !important;
  border: 1px solid {BORDER} !important;
  border-radius: 10px !important;
}}
details summary {{ color: {TEXT} !important; }}

/* ── File uploader ── */
div[data-testid="stFileUploader"] {{
  background: {BG3} !important;
  border: 2px dashed #F26522 !important;
  border-radius: 10px !important;
}}

/* ── HR ── */
hr {{ border-color: {BORDER} !important; margin: 6px 0 12px !important; }}

/* ── KPI cards ── */
.kpi-card {{
  background: {CARD};
  border: 1.5px solid {BORDER};
  border-radius: 14px;
  padding: 18px 20px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 4px 16px rgba(0,0,0,.1);
  margin-bottom: 4px;
}}
.kpi-card::before {{
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: #F26522;
}}
.kpi-card.hl {{ background: #F26522 !important; border-color: #F26522 !important; }}
.kpi-card.hl::before {{ background: rgba(255,255,255,.3); }}
.kpi-lbl {{
  font-size: 10px; font-weight: 700; color: {TEXT2};
  text-transform: uppercase; letter-spacing: .5px; margin-bottom: 7px;
}}
.kpi-card.hl .kpi-lbl {{ color: rgba(255,255,255,.8); }}
.kpi-val {{ font-size: 22px; font-weight: 700; letter-spacing: -.5px; color: {TEXT}; }}
.kpi-card.hl .kpi-val {{ color: white; }}
.kpi-sub {{ font-size: 11px; color: {TEXT2}; margin-top: 4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.kpi-card.hl .kpi-sub {{ color: rgba(255,255,255,.7); }}

label[data-testid="stWidgetLabel"] p {{
  color: {TEXT2} !important; font-size: 11px !important;
  font-weight: 700 !important; text-transform: uppercase !important;
  letter-spacing: .5px !important;
}}
</style>
""", unsafe_allow_html=True)

# ─── LOGIN ───────────────────────────────────────────────────────────────────
if st.session_state.user is None:
    st.markdown("""
    <style>
    .stApp { background: linear-gradient(135deg,#1a0a00 0%,#3d1500 50%,#F26522 100%) !important; }
    div[data-testid="stForm"] {
      background: white !important; border-radius: 20px !important;
      padding: 10px 20px 20px !important;
    }
    div[data-testid="stForm"] input { background:#F7F7F7 !important; color:#111 !important; border:1.5px solid #E0E0E0 !important; border-radius:8px !important; }
    div[data-testid="stForm"] label p { color:#888 !important; font-size:11px !important; font-weight:700 !important; text-transform:uppercase !important; }
    div[data-testid="stForm"] button { background:#F26522 !important; color:white !important; font-size:15px !important; font-weight:700 !important; border:none !important; border-radius:8px !important; }
    </style>
    """, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 1.1, 1])
    with col:
        st.markdown("""
        <div style="margin-top:80px;margin-bottom:24px;text-align:center;">
          <div style="display:inline-flex;align-items:center;gap:14px;background:white;
                      padding:20px 32px;border-radius:18px;box-shadow:0 8px 32px rgba(0,0,0,.25);">
            <img src="data:image/png;base64,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" 
                 style="height:64px;width:auto;object-fit:contain;">
            <div>
              <div style="font-size:22px;font-weight:800;color:#111;letter-spacing:-.5px;">
                UNIVISA <span style="color:#F26522">Receitas</span>
              </div>
              <div style="font-size:12px;color:#888;margin-top:2px;">Dashboard Financeiro</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        with st.form("login_form"):
            st.markdown('<p style="font-size:13px;color:#666;text-align:center;margin-bottom:4px;">Acesse com seu login e senha</p>', unsafe_allow_html=True)
            login_input = st.text_input("Login", placeholder="Ex: joao.silva")
            senha_input = st.text_input("Senha", type="password", placeholder="••••••••")
            if st.form_submit_button("Entrar", use_container_width=True):
                if login_input and senha_input:
                    u = do_login(login_input, senha_input)
                    if u: st.session_state.user = u; st.rerun()
                    else: st.error("Usuário ou senha incorretos.")
                else: st.warning("Preencha login e senha.")

        st.markdown('<p style="text-align:center;font-size:11px;color:rgba(255,255,255,.5);margin-top:16px;letter-spacing:.5px;">ASSOCIAÇÃO DO ENSINO SUPERIOR DA VITÓRIA STO ANTÃO</p>', unsafe_allow_html=True)
    st.stop()

# ─── APP ─────────────────────────────────────────────────────────────────────
user     = st.session_state.user
is_admin = user.get("role") == "admin"
nome     = user.get("nome") or user["login"]
iniciais = nome[:2].upper()

# ── SIDEBAR ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"""
    <div style="padding:20px 4px 16px;border-bottom:1px solid rgba(242,101,34,.3);margin-bottom:16px;">
      <div style="display:flex;align-items:center;gap:10px;">
        <img src="data:image/png;base64,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"
             style="height:40px;width:auto;object-fit:contain;filter:brightness(0) invert(1);flex-shrink:0;">
        <div>
          <div style="font-size:13px;font-weight:700;color:white !important;">{nome}</div>
          <div style="font-size:10px;color:#F26522 !important;text-transform:uppercase;letter-spacing:.5px;">
            {"Admin" if is_admin else "Usuário"}
          </div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🚪 Sair", use_container_width=True, key="sair_btn"):
        st.session_state.user = None
        st.session_state.dados = []
        st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # Abas da sidebar
    sb1, sb2 = st.columns(2)
    with sb1:
        if st.button("📂 Planilhas", use_container_width=True, key="sb_plan"):
            st.session_state.sb_aba = "planilhas"; st.rerun()
    with sb2:
        if st.button("👤 Usuários", use_container_width=True, key="sb_user"):
            st.session_state.sb_aba = "usuarios"; st.rerun()

    aba_ativa_sb = st.session_state.get("sb_aba", "planilhas")
    cor_plan = "#F26522" if aba_ativa_sb == "planilhas" else "rgba(242,101,34,.3)"
    cor_user = "#F26522" if aba_ativa_sb == "usuarios" else "rgba(242,101,34,.3)"
    st.markdown(f'<div style="display:flex;margin-bottom:12px;"><div style="flex:1;height:2px;background:{cor_plan};"></div><div style="flex:1;height:2px;background:{cor_user};"></div></div>', unsafe_allow_html=True)

    if aba_ativa_sb == "planilhas":
        st.markdown('<div style="font-size:10px;font-weight:700;color:#F26522;text-transform:uppercase;letter-spacing:.8px;margin-bottom:10px;">📂 Planilhas Salvas</div>', unsafe_allow_html=True)

    uploads = get_uploads()
    if uploads:
        for up in uploads:
            c1, c2 = st.columns([4, 1])
            with c1:
                label = f"📊 {up['ano']} — {up['nome_arquivo'][:18]}"
                if st.button(label, key=f"load_{up['id']}", use_container_width=True):
                    d, arq, ano = load_upload(up["id"])
                    if d:
                        st.session_state.dados = d
                        st.session_state.arquivo = arq
                        st.session_state.ano = ano or "2025"
                        st.rerun()
            if is_admin:
                with c2:
                    if st.button("🗑", key=f"del_{up['id']}"):
                        delete_upload(up["id"]); st.rerun()
    else:
        st.markdown('<p style="font-size:12px;color:#AA6644;">Nenhuma salva ainda.</p>', unsafe_allow_html=True)

    if False:  # users moved to tab
        st.markdown('<div style="font-size:10px;font-weight:700;color:#F26522;text-transform:uppercase;letter-spacing:.8px;margin:20px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(242,101,34,.2);">👤 Usuários</div>', unsafe_allow_html=True)

        with st.expander("➕ Adicionar usuário"):
            with st.form("add_user_form"):
                nu_login = st.text_input("Login")
                nu_senha = st.text_input("Senha", type="password")
                nu_nome  = st.text_input("Nome completo")
                nu_role  = st.selectbox("Perfil", ["user","admin"])
                if st.form_submit_button("Adicionar", use_container_width=True):
                    if nu_login and nu_senha:
                        if add_user(nu_login, nu_senha, nu_nome, nu_role):
                            st.success(f"'{nu_login}' adicionado!")
                    else:
                        st.error("Login e senha obrigatórios.")

        for u in get_users():
            cu, cd = st.columns([3,1])
            with cu:
                st.markdown(f'<div style="font-size:12px;color:white;font-weight:600;">{u["nome"]}<br><span style="font-size:10px;color:#F26522;">{"🟠 Admin" if u["role"]=="admin" else "⚪ User"}</span></div>', unsafe_allow_html=True)
            with cd:
                if u["role"] != "admin" and st.button("✕", key=f"du_{u['id']}"):
                    delete_user(u["id"]); st.rerun()

# ── TOPBAR ───────────────────────────────────────────────────────────────────
tema_ico = "☀️" if dark else "🌙"
tema_tip = "Modo claro" if dark else "Modo escuro"

st.markdown(f"""
<div style="background:linear-gradient(90deg,#1a0a00 0%,#3d1500 60%,#5a1e00 100%);
            border-bottom:2.5px solid #F26522;
            padding:0 20px; height:58px;
            display:flex; align-items:center; justify-content:space-between;
            margin: -0.5rem -1.2rem 1rem -1.2rem;
            box-shadow: 0 4px 24px rgba(242,101,34,.25);">
  <div style="display:flex;align-items:center;gap:14px;">
    <div style="background:white;border-radius:8px;padding:3px 6px;display:flex;align-items:center;">
      <img src="data:image/png;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCADKAVUDASIAAhEBAxEB/8QAHAABAAICAwEAAAAAAAAAAAAAAAYHBQgBAgQD/8QAVBAAAQMDAgMEBQUKCAoLAAAAAQACAwQFEQYhBxIxE0FRYRQicYGRCCMyobEVMzU2QlJic3SyFhdkcrPB0fAkJzQ3VIKSlNLhJUNEU2N1g6Kj4vH/xAAbAQEAAgMBAQAAAAAAAAAAAAAAAQYCAwQFB//EAC8RAQACAQMCAwcEAgMAAAAAAAABAgMEBREhMQYSQRMiMmFxgaEUJFGRI8E0UuH/2gAMAwEAAhEDEQA/ANy0REBERAREQEREBERAREQEREBERAREQERcOQCQOpCczfzh8V8ZnBoOemFBeH2p3XHVuo7BLN2zaCp5qZ569merT48ruYezAWm+auOa1t3ltx4b5K2tWOkLBBB6EIurRgrstzUIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiJkeKAiZHiiAushwMrkkAdVhdXX2ksFknudY/DIW5DQd3u7mjxJOyxvetKza3aGVKWyWite8sLxS1ZDpnT0kocDXTAspY89XfnHwA6kqu/k5wGS+3e4SP53thYC89SXuc52feM+9V5qe/V2prxLdbi4F7stjjH0YmH8kf29/VZXh7rKo0hW1kkdF6ZFUxgSR83K4OGeUj25PXr1Cp192pl10Xt8FeX0OvhzJg2q9KxzktxMtn4ZQ9wLSCPIr0KneBGr57hW11muTmiZ8j6qnwcgBzsvYM9wJyPIq4WnzVq0uppqccZKdpUXXaLLos04cveHKIi6XIImR4ogIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIg4d0Veav4hssd9ktUdtlqHQtaZHh7WjJGcDPkrDK1+4n76+uXdgxef5AXgeItbm0emi+GeJ54e1sWixazU+TL24lZen+IunbgWxy1DqKodgGOoHLv4Z+ifipjFNDJGHxvDmnvByFrNBVT04DGtiqIsl3o9RCyaN3j6rv6lYWhdR0EAbHy2Wibt83HWyU+PH5tzeX4FcW0+IP1E+zy93XuuxTpY8+PstU4wSVSHyjLlO652u0guEDY31Dx0a93MGtOfLc48wrkttxpLhCJKSohnb3uifztHvCh/GLSMuo7AyaijY+4UTu0haducflMz5gfFe3uWO2bTWjHP8A68/Zc+PTa2l8sdOf6UJptlqkvENNeu1FBIezLo3YfE7ZrXd4Iz+8pjxD0Np6whsduvf+HdiZ46Ooe0ukYPpcpAGPHHeoEyjrRco7c6iqG1naMj7F8Ra/JwMYPf59NvYpnxzpqml1jHPUNe2Kaki7N3KS3LMgjbvyenn5KoYeK6S9rY+Zj5dX0DW5vPr8VcebiLRPMc9PkjekbhLatVWuvgD3PjqWAsYMl7XeqW/Wts4D6gPkqI4I6LrKu8x6gudJJT0tL/krJWkGR+Mc+PAZ2J6q9HuEA5nn1QMk+CsexYb4sE2t2ntCp+LNXi1OriMfpHWX3DwBjKwWodV2OzNLK+4RxydRG08zz7AN1hNWaptwgfHFWWyQEdH3JzM+5jSSqnr7m6Wqc6ip7dSM3HaUlMGufn9Nw5sezGfqWvdd7rpI4p1lwbbs99bbr2T6p4sUzZvmLTUyQA/fC9rSR7PrVn0EjZqOKZpy2RocM+BWr0o+aPsJ9ux+tbN2L8DUf6lv2Bc3h3dM+uvkjLPbt93Xv+2YNFWk4o788vaiIrUrQiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiIOCtfuJv4/XP8A9P8AowtgStfuJ7mDXtzBc0feup/QCqvi3/hx9Vl8LT+9+0o6QCMHcLtDJJBIJYJXRPHRzeoXXIBwTg+CL57EzHWH0GaxaOJSmw6yuNG9rquetr3t6CesEUDPMgMzj2kq1NIaipr62WEVVNNUwtBmbTh3IzmyAA5wHN9E7qgsDpgYVk8Bf8tvH82H7ZFatg3bUTqa4LTzE8/hUN/2jT4sNs9I4mOPys/0OnMwnMLDINubG4Xaakp5wO2hY/l3HMM4X0nmihiL5Xta1oJJJwAF4bJerVeoDPaq+Cria7lLoXhwz7lfvd54lSucnf8Ah2u9XS2m2TVs7xDBC3mc7BIaPYFVmp9eTzZjppg5mcRVVuqi1/TJ5mOYce8H2hT7if8AiJds/wDcH7QqBIHO7bv/AKgqj4k3TNpL1xYunMcrR4e2vDq4nLl68T2+3L1XG4V9xeTW1tRUDPq9o8E/Fuy8w65787lEdgDKpF7WvPmtPK9Y8dcdfJWOIdZfvL/Jp+xbMWDey0f6lv2BayVL2NheC8A8pOM74wVs3p78CUf6lv2BW7wfHv5Psp/iy3u44+r3IiK9qWIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAeiwVbpWy1dc+uqbfBNO/wCm94yXbY38dlnUK15MVMkcXjlnTJek81nhSvFHRtPYqdl0tbSylc/lmgaCWsLvyh3jw+Cgcbg5oI6d2+dvatmbrTel00lN2hjL2kB4a1xb54cCD7wqm19ouht9HPcaGpldJCOeeFvZhuO849Xlz4DPfgdypG+7DNbTmwccesLjse+xERgz8z/EoCrH4Eu5K28Z/Nh+2RVuHZOO/r0VjcDgDX3cEdWw/bIvI8Px+/pP1ev4innQ2+z08Udd6dmtN80v6bPFcOwfCcROIa4syNx3b9eij+hdf6VtN0utdMySgiqRTNbAyHbLI+Uk8owDnYeOArYuFqtUcNZVGipxJK0mZ/IMv26k9+wUD4EW611eh52S0sUwNZKHh7AQQD6o+CvmSuf9TTr/ACpuK+m/SW9yekx+Um1xc6a68M66vpC4w1FIJIyRglpwRsqPd9In+/crz4hU0NNoC408EbY4mU4axjRgNAIAAVGO2APiMkqo+LOZ1FOf+v8AtZPCfHsb8duf9GVlNH2Z+pL/ABWxkhijAMs0g3IY3G3kSTgZ8yvToiwRahuE8VRUS09NA0GR7OUEZ8ebp7QCro0tYqWxwGCkk54DjkHIwcox4gZJPUkkrTtGzX1d4yX+D8t2873XTVthx9b/AIh5aXQum4aNlKLXTujbueYZLj4k96ktNE2GFsTQA1owAO4Lu3oFyvomHBixR7lYhQL5cmT47TIiItzWIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAukhIaSNyu66kEhBG9RapttlnENzlkpu0B7OUs5mnby8M96q3XMVPRRwTGppblPch2ksrIezhkY0nlLmtd6zvX2yO5XDqSzUt5t8lFVtcY5AQS1xa4ewhUhfNF3O1X2G1UzXVxqSGwytZgNbn8vY4AA6DAVY379V7PyUr5on1/j7er39kjTe0817cTH5YKMVFXVCKGKSaaXJbHGN9vIKx+BIIr702RhZI0QhzHNwWnMmxCzNrodL6Fp21NQ+P00gRzzlvNIS7fcAeqCR7OiwPC2/Rz63uz5wITcvXia7b6BO2/fh2V4u27fXRanHbLf3555j0h6+4bhbX6bLXHT3I44me89Vl6gpKistNXS0srYppoXMY5wJDSRjO3tUV4TaQuGkLfV0dZXR1TJphKwxtc3l9XB6+xTlrs4wCfcmduhV6nDS14v6wp1c964pxR8M9ZRzibj+AV3Pf6OcfEKh5IKllPFUPp5xBIeWKQtx2hx+Sf61dPFy5QQaOq6ZzvnakCKNvedxnbyGVhdG3nTt501SacvUcDpo43AxSt9UtYcB2TtuCO9VLe9Li1us8s34mK9Prysuz6zJotLN4p5omfxx3V/p50VVdoaOtZCY6vEL5C09O4PHMA5vUEd/erVs2ordZP+gKm5sr6+N3qRUtP2fI045W4BwNj1yq+1ho+ptl+hpLdFNPRVrh6PIQXCMnqCeuw6KxuH2iYNPxNnnl9Irpd5XA4jBx0a34b9U2PFrMN5wzX4fX5fI3rLpM9YzRb4vT15+aZUjnvbzOyCQNiei+4XVjSF2Cucc8dVV+giIpBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQERcO6IOkgDiN1hKqsZb6qpbU8wYB2kbgwuyCN27ZJIIJ6dCF6L1cBa3RVNQ8NpHHkleSAIvBx8u4+0KNXriHp6gDmNqTWzjI5IBzDPmegXDqtVhxR/ktw34MGXLbilZlWF8oLxer/X3KKzXGX0iYujzAQeUYAHrYwMAbe9ZG/aOqbDpikvE9XJDcRMwvjYRhpJ9XBx1b49PFSH+NiIucPuFKBkchErST5+xQ/VOrLhqY07apsUEMJL2xwvJDnEDc+OMlUTV20ERbJXJN7T2XbSY9yt5MNqeSkd/mzdn4m32kgZDW00FeWjHaD1HH27Y+teqr4qXF7C2ntEUbsZy+Xmx8Aq/wCUYxhcFreXHKPguKm9a+tPJ7Tl6Ntg0NreaKcJLYoK3XOqBFebi8lkJeOzAaBgnZngOm+57ui8N90he7fV1FLJapqmDmcGSxRhwkaTkHYbHbwXitNwqbVc4LjRuaJoCS1rjhrsg5acd2/xwp7ScVnspy2qs3azgn73KA0/HfPuW7S20Wor+5vMX555c2rxa3S5P21Itj444SLR97kfpKGGppKltwpYxF2UkTml7gMDGRg523HTdTChb2UUbHHJaME+J71ArTxNtFVL2dwpai3uPRzvXZ8R0+CklHf6K6VENPaK2Gpc8c8j4iHCNnnjoSdgD5nuV60GrwXrWKX8ykazTZ8d5m9Jr1SVF0iJxuuzei9ZwuUREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEKIgi/Eiw1uoLEaOhquwlDublcAWS/ou8v7FRFzoKy0VRpLjSyUsgJwHDZw8iNj7ls7I3OF4LlaaG4wGGupoqiM9WyNyFXt42Ouu9+tuLR/T29p3q2g92Y5r+WtYOd8nB7+4+9c5791bl54V2udzpLZVVFE8nPKT2jPr3HxUYruGGoYHE01TQVI6YcXRnHuaVTc3h/XYe1fN9FwweIdFm728v1QvK4ypK7QWrWk5tsTsHALalpB+OF8hojVTiQ22M28alg/rXHO36uJ49nP9O2N00c9YyQj+R4pnbc4HTfZS6k4baonx2rKGmae985cR7g3H1rO2zhSebmuV3e8HqynZy595yfgunFsevzT0xzH16OXPv+hxRz5+Z+Sshl0rYmsc6R5w1jW5c4+Q71aPCfSN3t1yF4rXOo2OjLRTbF0gPQv8PIdfPuUysOkrJZmj0GijbJ3yu9Z5/wBY7rPMYGuGFbNp8O/prxlzT70eir7t4gnVVnFirxWf57voEb0XKK1KyIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIuH/RKDlFr5fuKes6W/3Glp6mhjggqpYo2ml5iGteWjJ5vJevQ/EzVtz1larZXz0ctNVTiORrKblPKWuOQc7HLfBBfCLh2eQqgtU8T9YUOp7rQ0lRRRwU1XJDG11NzHlacDfKC/kVDaL4mauuer7Vba6oopKaqqBFK1tNynBB6HmyDnHcpLxn1nqDS9yttLZ5aaNtRDJJKZYeckhzQANxjqfggtRFrizi5rWOWN8k1vnja8F0XovLzjO7QQdieivfSd7or9Y4LrQyF0M7c4d9Jh6FpHcQdkGXRU5xb19qXT2rjbbRNSxU4pmSfOQc7nOPN5jb1VH7DxT1nVX+3UlRU0L4Z6qOKRopMEtc4A4OUGwZXGAq3406tvml4LV9xpYInVUkokdLFz7NAxjfzVcDizrgj/LqD/c/+angbHloxhfORnqjCqmfWOq5OEFLqqkfSivbUEVJMOW9kJHsyG5xn6JO/cVCf42dbkA+m0BG3/ZAP61HBKT/AChIrhRTWy60lfWwRuDoJGRVD2NLvpNJAIHcVAtAPvF21nbKI3e5uZ27ZJB6XIQWMOTn1u/A+Kl8V8r9fcO7/RXPsJLpb+SpgfCzkDgNxtk4Ozh7CsZwc7G20OoNXzNBZb6UsgB6F59Y/EhoVX1mnvO4UrEzxPVddDrsVNmyTeI81en9thI4xgDf3lfRrAtb4+K+uM5Nbb2kkeqKTIBPd19gUz4Sa01dqfVEtLcZqWWggpjJM6On5CHEgMAOe/1j06BWeI6cKV1nrK3w0Bc4WvFz4q60iu1dDBU0McMVVLHG00uSGteQASTucBe/RPEzV1z1fa7bXVFFJTVU/ZyNbTcpwQehzssuBfCLwXStpbZQS11fUsp6eIF0kjzsAqf1HxpqXvdHpu2MEefVqazPrDxDBg7+ZB8lHHIu9Frc7i3rcvz6XQMP5opP/sspZuM+oIJg260FHWw5+lBzRPA95I+OB5p5RfqKP6N1bZ9UW41ltqCXNOJoJByyQu8HD+sZB8VnyfUJG+2dkHKKn+LXEO92DUsVosclOzs4BJUvli7Q8zj6rRuMYAz7wof/ABta5/02g/3P/mnA2QRUFpPirqWXU1BT3ioon0E87YpuWDkc3myAQc9xx7sq+2EEDdTMcDsiIoBERAREQEREBERAREQEPREPRBqPqc51TeP2+f8ApHLIcNP841g/a2/uvWP1P+NN48rhUD/5HL38ND/jGsP7Y0f+139qzjshtSVqdrf8eL9/5hL+8VthnZana2OdcX7HT7oTfvLGCXp4bD/GJYD/ACxqmvyk/wAYLL+zSfvNUK4bn/GFYP21v2FTX5Sf4fsv7NJ+81T6pVWd1M+EmsDpW+CmrX4tNa7Ew7oZCdnjyOcHw2PshbjgZOcDrjwXDmteHsd35Y4EYPeCCD07/apQn3HtzXcQeZrgR6DFjB65LsKKaX/Gm0ft0X7wXlrqyqrhTmrlMpp4BTxE9RGM4B8cZ716tL/jVaP26L94JxwlafylvvGnv1s37rVTTPoq5flLfeNPfrZv3WqmmfRSES2B4S2+C7cHobbVMDoakVMbwe4GV/2Kgquknoq2ooakEVFNM+KTPXIOM+85Psx7tieBmf4sqAjc9pP/AEr1WXHizi361Zcoh8zc4ecnH/WMwHD4EH4+6I7pY3g7cRRa7gpJcGC4xOpZR4kjmaf9poH+spBxHoI9H6AptMRPBmuFfLO8tPWJry4Z+MY+KrOGaWlqYquAkTQPbKzHXLSD/UpZxav8eodUxz0sgfSQUUIiIOd5Gh7vtaPcsZx1m8XmOsMoy3ik056T1lEACP7/AN//ANV/fJ9sooNHG6PA7W5SmRp/8Jvqs+IGfeqJtlBNdbnSWunaXTVc7ImAeZ3PuGT7lttaqWKht8FDC1rYqeNsTABgABuFlLHmZ6y1NvH4buWf9Nn/AKVyy3DY44hWA/yxv2FYm8fhu5ft0/8ASuWW4bDPEKwD+WN+wqfRCxPlKVsopLLbA54gmkkmkH5LyzkAB8fpE48lTUfMWguOSd8rZTivo92rLCyGllbDX0rjLSvePVLiMFru8A+PkFr3erJebLOYbpaqqkdnYmMlhHiHgFuPeoiUs3pTQ82o7VHWUepLNBK8HFJJMTI3yd4H3FJ+G+t4pnxmyPl5TjnZMwtcPEbqHuMEzjzdm92cDO5+te6huNyoHNdb7lXUhb0EU7mjPjjOEQn/AA30lreyazoK42qWmpnOMVU4zM5XREHIIBydw0jwV9STxwU7nyENZGzLj3AAZVI8NuKN0F4prTqORlXBUOEUdXyhskbz058bEE4HTqptxqvLrRoWqbE/knrXCljwd/W+lj/Vyo4SoPUNzdetQV93cHF1XOXsBO/L0YP9nl95K+cFDVT2quukTGmloTEJnE4PzhIbj4b9OoXka0Ma0N6NwPHwx9gVy8N7fp08Kaq2XC522KpvDJHytkqWNcMjEeQTtgBp96lipl7S5pGcHxz3jp7MFbUcObyL9o62XPI7SSHlmAPSRuWuHxBWrEZfyfOEOeCeYgg5Od9xkK4/k33fH3T0/LJkRkVUAPeDs/Hvwfekphc6IN0WKRERAREQEREBERAREQF1lz2Zwuy4cMjCDVniVbJbRru708gIZLO6phcT9Jkh5s+4lw9ywlsrqi23WluVG8CellEsZI5m5HXOO49M9y2U4haGt2r6Rjag+jVkOewqmNBc0bZa4HZzTjofqVM3rhZrK3TuENJHdIxs2Wnkw53mQ85z7SfasokSZ/G+oNvDWaf5a0jHMagGIH4ZVUVFRPV1c9XVPD6mokdNK7BHM5xJJx3ezuWc/gRrPm7MaZuBd/Nbj48yzVj4UavuMzW1UMNsh6ufM7mLfY1p3P8AfYqeg8nBy3S3HiNbixuY6LmqpnZ+iA0hvxJHwUm+Ul+H7J+zSfvNVmaD0bbdJW001E0yzyHmnqXgc8jvd0Hl7fFQbj1p6+Xi7Wme02uormRQyMkMWDykkEZyfJY89RTA++R7n743od/pBWjxo0SadjdV2uFzo5A37oRM/J2++Adwz9IjcdQoc3Q+snSRj+DVePnG5JDcDfr1Wzbads9E2CaNr2GMMexwyCMYI81MyjhqG05YHFwORnKyWl/xptH7dF+8FKtd8M71ab5INP22or7bM7niEPKTBvuwgnceHwXj05ovV0Wo7ZNNp2uiijq4nve8Nw1ocCSd05SmvylvvGnv1s37rVTTfoq9ePtkvF6p7MbRbp63sJZe1EWMt5g0A7nyKqpuiNZAfizcf9lv9qRKOF3cCP8ANpb/ANbP/SvXy47WP7p6EmqYGD0i3OFSw46NGz/b6pJx34WQ4P2+utOg6ChuVM+lqWulc6J+MtDpHEZx5EKVVsUdRSyQTNDo5WFj2kZBBGCohLTtuHNOWnr0Jz37fDp7QVzhoz081KLnw+1ZQXKpo6SxVtTTRSuEErOUtdHn1e/rjGfPK850TrLBxpm45/mt/wCJZcwjhJvk/wBmFfqye7yMcY7dFyx56do7I+IHMD7Qr/YMZUN4O6bn03pGKnrIuyraiR09Q3YlpOAGkjrgAD4qaOIBCxlLUO8fhu5ft0/9K5ZXhv8A5wbDuR/hjensK9V30Vq914r3x6crnsfVzPY5obhzXSOIPXwIWT4f6P1VS64s1XV2Csp6eGpEkssgaGtaAeu6nnojhL+LGu9T6X1FDQ0LKFtHLTiRr54S8udnDhnnHTb4qIO4uawcA2WOzSR53YaM4PkfnNldGt9J23VtrFJWgxyxkugqGAc8TvEZ2I2GQcg4CpS+cJ9XW2RzaSKC6QgbPhfyOO+/M1x6+eVEJZKTiHpC40rRetBwz1GBz9kxnLnyyA73Yyq9vE9FU3WpqbdQG30b3ZipjKXmMd+58dtu7Cyz9E6xYeV2mbgPY1pHx5l77dw01rXytYbUKNpIy+pmAAHjhpJWXQRWggmqq+mpaVpdUSzsZEAd+YkAEezr7vJWD8oC7ms1PR2Zh5m22Dnk8O0fj6w0fWVO+HvDOh0vVtuldMLldA0hknJhkXjyD849OYqq7/pfXFzv9wuVRpy4PfU1D35AbjlJ9UdegaAFjyImQC05xy438PBdXwxnmL4mevknLeuSc/WSrE4acO7tU6pgm1HY3w22ma6RzKqNpE7iMNaRkjbJPuCuFuhNG9TpSy7/AMjZ/YsuYRw1cJ2Lie/c+f8Ac/Ws7w7vBsOtbbcHbRdr2E5/Qk2P18vwCsPjDw8B9Br9JWKJjo+aGogo4mM5gd2vxt0OfiPBV2/Q+s3MLf4NXIZHUBv/ABKOUtqYjnODkLusLoiS4y6Wtz7vTvp7h6OwVEb+oeBg59qzSxBERAREQEREBERAREQFw93K0lcriQAsOUHx9JbzFuW5Ay4c24HmunpEbySHMd3bOB37lUtFT2c2i+XOerLL8+S7sjLZSHzMaXbOHeGDlx4ZOOq+N3o5abS+lZLXb6K33B9c2oLKKTnbM6KnfI3ndgbuazG46lBcD6mEDDnxgHxcFy2ZgdhjmOPeAVTtoo7VfNN6Qr6igjmFZfqlp7VgcTE51S7s3Z6jPcfALx1gnj1vc5KeFtI9lxrZI69j3c7+zom/4Py9MHnDxnI+bdsgvCOoa8ZbynBwd+h8F1M8XUvZg755uoVa8K4qWC+djQub2MunaGolDJC4PlcZMyHc5cQBknc4GcqI3SYv0zCGTOdyaauocGybhwqYwM4Oc9R5IL4fLHj6bQRjvHf0XLaiJoGJGEOHMDzdypezzz1N9mo618rp6O82yinJOO0MbZRzewgB3vXmmimOndU0kzsusFrqLeSZHABz6hzxk93zbYznu5kF2mohJDy9gycfSHXwX0fLHnky3mI6Z3KqHX1HBQ2yz9lpq1nENdPU0VNOWxtIiBLw5rQS8DfoD4L5UlLBbdTWya4XD7sF/oUBrY5+Wpo5+wwxrm7c0EvUgdC7cEdAuA1EIxzSRZ7suH1L5Q3GKWqqYBFIz0ctDnvbysJIzhp78bfFVXabTbKug4fS1dHDO+Yysme9vMZGtjeQHHvAO4WGfd2VdDq50EplddKb0oxOL2jnZVOiABI2aY+yzjbv3ygvRkjPVLS0gnbBGCu3bMkHquacbZBzg+CpKDt6drZWNbQYuVzbHRwyFzaTs6RzeVp8CW84GABzKS8LoaelvVbTW/ApHWO31DmseXNMzmycz+p9Y4GTndBY0crDnkLHAHfB6LsZY+y5yWhpHXO3xVG0tTFZbW+toJBBNUWGqNRyP2fMapscbnDf1hzkZ6npvgYl2jaeK7cKrnp6GZ8rab0ugieS4PLWlwjduA4EtLCgsLt42Hl5mA7DHN4r41FdE2J0gcxwY0ucGnmOB12H991UmmKp16ZZ79MXONdfIINzjLIqUsIx5uL1zcLTZ6SG7tZRQQxu1PTUb+X1fmHPiJiz+YTjLehQWxQ18dRSQVBidAJmhzWSjleAcbEeO69TpIwC4uaA3rk9FQN/hjbb5YqWCCqpKQXxsbH1Ba2mY2SEB8bhuOzJOACC0ZAIxhZbURjNJfrLU3J7TX3GgpnTtL+Z7W0rHuI5dxkMz78nvKC5+2EbQ53K0Hbd2AEfPFyhznsaHdMuxlV7d66C+cNdL1tXymKtrbcZw84a7MjOYHyJyCOncorZaeGsuthon0VHcaRkt2jEFbN822FlYxrSMtIPK3Zo+tBdZmjY0F7mNycAkgZK7Oka12Mtz1wSq34v0lLIKGeQRVdPS0tS6W3mo7GSSHlbzTQO2xLGPo7j6XdsRjPutTS8TqedlbK6P0dtsiY/O7X0xm53HGAc8g65y7p0QWu6eMjJewAjP0hghcioh5M9owt6ZDwqU4TROrK23WS8Rispm6ffPH2vrskhmfC9oIPUtf2jfcF9bbbLWbBpiKengFPU2+vnmDthJKxoDHuOd3NHQ9R3ILoEjWjmdygd56BdnTtB6twBk+t3eKq++VjZeEGn6C4Vc0brvHTU80xLu0DOUPefV3zhuMjxyonqu7VFTahqCCWV0b9Mx09XG0kffC+Mux4slYzw2LkF7vqIjzHnYXD8kOBPsXltl2iuED5TTVFKGP5HCpi7M58s9Rv1UFuVJZ7ZrOqvElBTj0aw+nOdyDPatcfXH6WABlRfTU1HVR2bT1bMKmiF6D6hsrnckva0j5S1wdgkCXtMA7bAjpsF5wHIOF9FFeEzzJw9tDu0dIOww1znl+WhxDdz1GAN1KkBERAREQEREBERAREQFw4ZGM4XKIMQ3TNgbcKq4i0UIrKthjqJxA0PlaeocepGwXyt2k9O24x/c+zW+lEcvbMEVO1vK/lLeYefK5w9hWcRBjIbFa4aenp4aGmjippTNCxsYDY3kklzR3H1nb/pFcOsFqNR25oabtfSfSufshzdry8vPn87l2z4bLKIgxVl09Z7KZvuRbaOhE7y+UQQhnOc9ThfFmlbAyatmbaKAPrwRVkU7czAnPrHvWbRBjH2G1Pqn1ZoKb0l8jJXS9kOYvYCGOJ8Rk49qTWG1SxVsUlBSujryDVtMQIn2x63jsAPcsmiDDW/S9ht8bI6G1UVMxheWtjhDQC8Yd8R18V1h0pp+GqpquO0UDaikiENPIKdodFGBgNae4YWbRBjmWS2MbSNZRU7W0eTTARj5kkEHl8Mg4+K+EmmrJJFDG+10RZBEYYm9iMMjJBLR4AlrT7gswiDGGw2s1Tqo0NL27pDKZOyGS8s5C72lux8lxZdP2iyxzR2q30lE2dwdIIYg3mOMZOOqyiIMHTaS05StmbS2W3wiaRskgbTt9d7X87SfEh2481kKS20dJLUzU1PFFLVSdrUPYwAyP5Q0Od4nlAGfJexEGKptP2imgp4Ke30sUVNOZ4GMiAEchJJePBx5jv5ld57Faailq6Wot9NNBWO56mN8QLZXYAy4d+wCySIMTFpuyRU8dPHa6JsUUL6eNjYAAyN+OZgHgcDI78LtDYLTBVtqoaCljmYcteIhzA8nJnP83b2bLKIgxVRp2zVFpbaZ7bSS0DXB7ad8QMYcHcwOPbuvLVaM0vVUlPSVNhts0FMwxwRvpmlsbT1AHcCs+iDE3fTllu8ENPdbZR10MDg+Fk8IeGOHeMrtJYLVJG5jqClIdUNqt4R99bjD/5wwN/JZREGMorFa6J8L6WhpYXQU/o0RZEAWRdeQeDfJfCt0rYKy3Q26qtNFPSU7uaGGSAOZGd/oju6n4rNIg8T7XRPfTPfTQudSEmnJYPmiWluW+Hqkj2FeN2l7C6nqKc2mh7KpjMczOwHK9pcXYI7xzOcfaSsyiDHVlktlX2hqaKnmMsPo7+eMHmjznkP6Oe5ea46XsVxZMyutVDUtnDGyiWAOD2szyA564ycLNIg+NFSwUdOympomRQxtDWRsbhrQO4BfZEQEREBERAREQf/2Q==" 
           style="height:38px;width:auto;object-fit:contain;">
    </div>
    <span style="font-size:17px;font-weight:700;color:white;letter-spacing:-.3px;">
      Receitas
    </span>
    <span style="background:rgba(242,101,34,.25);color:#FF8C42;font-size:11px;font-weight:700;
                 padding:3px 12px;border-radius:20px;border:1px solid rgba(242,101,34,.4);">
      {st.session_state.ano}
    </span>
  </div>
  <div style="display:flex;align-items:center;gap:10px;">
    <div style="background:#F26522;width:32px;height:32px;border-radius:50%;
                display:flex;align-items:center;justify-content:center;
                font-size:13px;font-weight:700;color:white;">{iniciais}</div>
    <span style="font-size:13px;font-weight:600;color:white;">{nome}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# Botões de ação abaixo da topbar
ba1, ba2, ba3, ba4, _ = st.columns([0.18, 0.18, 0.18, 0.18, 4])
with ba1:
    if st.button(tema_ico, key="tema_btn", help=tema_tip):
        st.session_state.dark_mode = not dark; st.rerun()
with ba2:
    db_label = "🗄️" if st.session_state.aba != "banco" else "📊"
    db_tip   = "Banco de Dados" if st.session_state.aba != "banco" else "Voltar ao Dashboard"
    if st.button(db_label, key="db_btn", help=db_tip):
        st.session_state.aba = "banco" if st.session_state.aba != "banco" else "dashboard"
        st.rerun()
with ba3:
    if is_admin:
        usr_label = "👤" if st.session_state.aba != "usuarios" else "📊"
        usr_tip   = "Gerenciar Usuários" if st.session_state.aba != "usuarios" else "Voltar ao Dashboard"
        if st.button(usr_label, key="usr_btn", help=usr_tip):
            st.session_state.aba = "usuarios" if st.session_state.aba != "usuarios" else "dashboard"
            st.rerun()
with ba4:
    if st.button("🚪", key="sair_top", help="Sair"):
        st.session_state.user = None
        st.session_state.dados = []
        st.rerun()

# ── ABA BANCO ────────────────────────────────────────────────────────────────
if st.session_state.aba == "banco":
    st.markdown(f'<h3 style="color:{TEXT};margin:8px 0 16px;">🗄️ Banco de Dados — Planilhas Salvas</h3>', unsafe_allow_html=True)
    uploads = get_uploads()
    if not uploads:
        st.info("Nenhuma planilha salva no banco ainda.")
    else:
        for up in uploads:
            c1, c2, c3, c4, c5 = st.columns([3, 1.2, 1.5, 1.2, 1])
            with c1:
                st.markdown(f'<span style="font-weight:600;color:{TEXT};font-size:13px;">📊 {up["nome_arquivo"]}</span>', unsafe_allow_html=True)
            with c2:
                st.markdown(f'<span style="color:{TEXT2};font-size:12px;">📅 {up["ano"] or "—"}</span>', unsafe_allow_html=True)
            with c3:
                data = up.get("criado_em","")[:10] if up.get("criado_em") else "—"
                st.markdown(f'<span style="color:{TEXT2};font-size:12px;">🕐 {data}</span>', unsafe_allow_html=True)
            with c4:
                if st.button("📂 Carregar", key=f"banco_load_{up['id']}"):
                    d, arq, ano_up = load_upload(up["id"])
                    if d:
                        st.session_state.dados = d
                        st.session_state.arquivo = arq
                        st.session_state.ano = ano_up or "2025"
                        st.session_state.aba = "dashboard"
                        st.rerun()
                    else:
                        st.error("Erro ao carregar.")
            with c5:
                if is_admin and st.button("🗑", key=f"dbdel_{up['id']}"):
                    delete_upload(up["id"]); st.rerun()
            st.markdown("<hr>", unsafe_allow_html=True)
        st.info(f"Total: **{len(uploads)}** planilha(s) no banco.")
    st.stop()

# ── ABA USUÁRIOS ──────────────────────────────────────────────────────────────
if st.session_state.aba == "usuarios":
    st.markdown(f'<h3 style="color:{TEXT};margin:8px 0 16px;">👤 Gerenciar Usuários</h3>', unsafe_allow_html=True)

    if not is_admin:
        st.warning("Apenas administradores podem acessar esta área.")
        st.stop()

    # Adicionar novo usuário
    st.markdown(f"""
    <div style="background:{CARD};border:1.5px solid {BORDER};border-radius:14px;padding:20px 24px;margin-bottom:24px;">
      <div style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:16px;">➕ Adicionar Novo Usuário</div>
    </div>
    """, unsafe_allow_html=True)

    with st.form("form_add_user"):
        col1, col2 = st.columns(2)
        with col1:
            f_login = st.text_input("Login", placeholder="Ex: maria.silva")
            f_nome  = st.text_input("Nome completo", placeholder="Ex: Maria Silva")
        with col2:
            f_senha = st.text_input("Senha", type="password", placeholder="••••••••")
            f_role  = st.selectbox("Perfil", ["user", "admin"])
        if st.form_submit_button("✅ Adicionar Usuário", use_container_width=True):
            if f_login and f_senha:
                if add_user(f_login, f_senha, f_nome, f_role):
                    st.success(f"Usuário '{f_login}' adicionado com sucesso!")
                    st.rerun()
            else:
                st.error("Login e senha são obrigatórios.")

    # Lista de usuários
    st.markdown(f'<div style="font-size:13px;font-weight:700;color:{TEXT};margin:8px 0 12px;">👥 Usuários Cadastrados</div>', unsafe_allow_html=True)
    users_list = get_users()
    for u in users_list:
        cu1, cu2, cu3, cu4 = st.columns([2, 2, 1.5, 1])
        with cu1:
            st.markdown(f'<span style="font-weight:600;color:{TEXT};">{u["nome"]}</span>', unsafe_allow_html=True)
        with cu2:
            st.markdown(f'<span style="color:{TEXT2};">@{u["login"]}</span>', unsafe_allow_html=True)
        with cu3:
            badge = "🟠 Admin" if u["role"] == "admin" else "⚪ Usuário"
            st.markdown(f'<span style="color:{TEXT2};">{badge}</span>', unsafe_allow_html=True)
        with cu4:
            if u["role"] != "admin":
                if st.button("🗑 Remover", key=f"rm_{u['id']}"):
                    delete_user(u["id"]); st.rerun()
            else:
                st.markdown('<span style="font-size:11px;color:#AAA;">Protegido</span>', unsafe_allow_html=True)
        st.markdown(f"<hr>", unsafe_allow_html=True)
    st.stop()

# ── UPLOAD ───────────────────────────────────────────────────────────────────
with st.expander("📁 Carregar nova planilha .xlsx", expanded=not bool(st.session_state.dados)):
    uploaded = st.file_uploader(
        "Arraste ou clique — formato Relatório de Receitas Líquidas UNIVISA",
        type=["xlsx","xls"], label_visibility="visible"
    )
    if uploaded:
        import openpyxl
        wb = openpyxl.load_workbook(uploaded, data_only=True)
        raw = tuple(tuple(c.value for c in row) for row in wb.active.iter_rows())
        dados, ano = parse_sheet(raw)
        if dados:
            st.success(f"✓ {len(dados)} registros de **{uploaded.name}**")
            cs, ci = st.columns(2)
            with cs:
                if st.button("💾 Salvar no banco", use_container_width=True):
                    uid = save_upload(uploaded.name, ano or "2025", dados, user["id"])
                    if uid:
                        st.session_state.dados = dados
                        st.session_state.arquivo = uploaded.name
                        st.session_state.ano = ano or "2025"
                        st.rerun()
            with ci:
                if st.button("👁 Visualizar sem salvar", use_container_width=True):
                    st.session_state.dados = dados
                    st.session_state.arquivo = uploaded.name
                    st.session_state.ano = ano or "2025"
                    st.rerun()
        else:
            st.error("Formato inválido. Verifique se é um Relatório UNIVISA.")

# ── DASHBOARD ────────────────────────────────────────────────────────────────
dados = st.session_state.dados
if not dados:
    st.markdown(f"""
    <div style="text-align:center;padding:80px 20px;">
      <div style="font-size:52px;margin-bottom:12px;opacity:.3;">📊</div>
      <p style="font-size:16px;font-weight:600;color:{TEXT};">Carregue uma planilha para visualizar os dados</p>
      <p style="font-size:13px;color:{TEXT2};">Use o painel acima ou selecione uma planilha salva na barra lateral</p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# FILTROS
cats  = sorted(set(r["nome"] for r in dados if r["nivel"]==0))
areas = sorted(set(r["nome"] for r in dados if r["nivel"]==1))
fc1,fc2,fc3,fc4 = st.columns([2,2,2,3])
with fc1: f_cat  = st.selectbox("Categoria", ["Todas"]+cats)
with fc2: f_area = st.selectbox("Área", ["Todas"]+areas)
with fc3: f_mes  = st.selectbox("Mês", ["Todos os Meses"]+MESES)
with fc4: f_q    = st.text_input("Buscar curso", placeholder="Nome do curso...")

filtered = [r for r in dados
            if (f_cat=="Todas" or r["categoria"]==f_cat)
            and (f_area=="Todas" or r["area"]==f_area or r["nome"]==f_area)
            and (not f_q or f_q.lower() in r["nome"].lower())]

mes_sel = None if f_mes=="Todos os Meses" else f_mes
def gv(r): return r["meses"].get(mes_sel,0) if mes_sel else r["total"]

# KPIs
cursos = [r for r in filtered if r["nivel"]==2]
vals   = [gv(r) for r in cursos]
total  = sum(vals)
maior  = max(cursos, key=gv, default=None)
pos_t  = sum(gv(r) for r in cursos if "PÓS" in r.get("categoria","").upper())
media  = total/len(cursos) if cursos else 0
qtd    = sum(1 for v in vals if v>0)

st.markdown("<br>", unsafe_allow_html=True)
k1,k2,k3,k4,k5 = st.columns(5)
for col, lbl, val, sub, hl in [
    (k1,"Total Geral",fmt_short(total),f"{st.session_state.ano}"+(f" · {mes_sel[:3].capitalize()}" if mes_sel else ""),True),
    (k2,"Maior Receita",fmt_short(gv(maior)) if maior else "—",maior["nome"][:28] if maior else "—",False),
    (k3,"Qtd. Cursos",str(qtd),"com receita",False),
    (k4,"Pós-Graduação",fmt_short(pos_t),"total pós",False),
    (k5,"Média por Curso",fmt_short(media),"receita média",False),
]:
    with col:
        st.markdown(f'<div class="kpi-card {"hl" if hl else ""}"><div class="kpi-lbl">{lbl}</div><div class="kpi-val">{val}</div><div class="kpi-sub">{sub}</div></div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# GRÁFICOS
g1, g2 = st.columns(2)
with g1:
    st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Top 10 Cursos por Receita</p>', unsafe_allow_html=True)
    top10 = sorted([(r["nome"].replace("Bacharelado em ","").replace("Licenciatura em ","")[:35], gv(r)) for r in cursos if gv(r)>0], key=lambda x:x[1], reverse=True)[:10]
    if top10:
        df_t = pd.DataFrame(top10, columns=["Curso","Receita"])
        colors = [f"rgba(242,101,34,{1-i*0.08})" for i in range(len(df_t))]
        fig = go.Figure(go.Bar(x=df_t["Receita"], y=df_t["Curso"], orientation="h", marker_color=colors, marker_line_width=0))
        fig.update_layout(showlegend=False, height=280, margin=dict(l=0,r=0,t=0,b=0),
            plot_bgcolor=CHART, paper_bgcolor=CHART, font_family="Sora",
            yaxis=dict(autorange="reversed", tickfont=dict(size=10,color=TEXT2)),
            xaxis=dict(tickfont=dict(size=10,color=TEXT2), gridcolor=GRID))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

with g2:
    st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Distribuição por Área</p>', unsafe_allow_html=True)
    by_area = {}
    for r in cursos:
        if r.get("area") and gv(r)>0:
            by_area[r["area"]] = by_area.get(r["area"],0)+gv(r)
    if by_area:
        pal = ["#F26522","#FF8C42","#C84E00","#FFB380","#E05A00","#FFD5B8","#A03C00","#FFC4A0"]
        fig2 = go.Figure(go.Pie(labels=list(by_area.keys()), values=list(by_area.values()),
            hole=.6, marker_colors=pal[:len(by_area)], textfont_size=10))
        fig2.update_layout(height=280, margin=dict(l=0,r=0,t=0,b=0),
            legend=dict(font=dict(size=10,color=TEXT2), orientation="v"),
            paper_bgcolor=CHART, font_family="Sora")
        st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar":False})

st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Evolução Mensal</p>', unsafe_allow_html=True)
vals_mes = [sum(r["meses"].get(m,0) for r in cursos) for m in MESES]
fig3 = go.Figure(go.Scatter(x=MESES_SH, y=vals_mes, mode="lines+markers",
    line=dict(color="#F26522",width=2.5), fill="tozeroy", fillcolor="rgba(242,101,34,.08)",
    marker=dict(color="#F26522",size=6)))
fig3.update_layout(height=160, margin=dict(l=0,r=0,t=0,b=0),
    plot_bgcolor=CHART, paper_bgcolor=CHART, font_family="Sora", showlegend=False,
    xaxis=dict(gridcolor=GRID, tickfont=dict(size=11,color=TEXT2)),
    yaxis=dict(gridcolor=GRID, tickfont=dict(size=10,color=TEXT2)))
st.plotly_chart(fig3, use_container_width=True, config={"displayModeBar":False})

# TABELA
st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Tabela de Receitas</p>', unsafe_allow_html=True)
if mes_sel:
    df_show = pd.DataFrame([{"Centro de Custo / Curso":r["nome"], mes_sel.capitalize(): fmt_brl(r["meses"].get(mes_sel,0)) if r["meses"].get(mes_sel,0) else "—"} for r in filtered])
else:
    records = []
    for r in filtered:
        row = {"Centro de Custo / Curso": r["nome"]}
        for m, ms in zip(MESES, MESES_SH):
            row[ms] = fmt_brl(r["meses"].get(m,0)) if r["meses"].get(m,0) else "—"
        row["Total"] = fmt_brl(r["total"]) if r["total"] else "—"
        records.append(row)
    df_show = pd.DataFrame(records)

st.dataframe(df_show, use_container_width=True, height=380)

st.markdown(f"""
<div style="padding:8px 0;font-size:11px;color:{TEXT2};display:flex;gap:22px;flex-wrap:wrap;margin-top:6px;">
  <span><strong style="color:{TEXT}">Arquivo:</strong> {st.session_state.arquivo or 'Nenhum'}</span>
  <span><strong style="color:{TEXT}">Registros:</strong> {len(dados)}</span>
  <span><strong style="color:{TEXT}">Usuário:</strong> {nome}</span>
  <span><strong style="color:{TEXT}">Atualizado:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</span>
</div>
""", unsafe_allow_html=True)
