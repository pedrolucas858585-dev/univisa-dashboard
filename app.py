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
            <div style="width:52px;height:52px;background:#F26522;border-radius:13px;
                        display:flex;align-items:center;justify-content:center;
                        font-size:20px;font-weight:800;color:white;">UV</div>
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
        <div style="width:40px;height:40px;background:#F26522;border-radius:10px;
                    display:flex;align-items:center;justify-content:center;
                    font-size:16px;font-weight:800;color:white;flex-shrink:0;">{iniciais}</div>
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
    <div style="width:36px;height:36px;background:#F26522;border-radius:9px;
                display:flex;align-items:center;justify-content:center;
                font-size:15px;font-weight:800;color:white;">UV</div>
    <span style="font-size:17px;font-weight:700;color:white;letter-spacing:-.3px;">
      UNIVISA <span style="color:#FF8C42;">Receitas</span>
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
ba1, ba2, ba3, _ = st.columns([0.18, 0.18, 0.18, 5])
with ba1:
    if st.button(tema_ico, key="tema_btn", help=tema_tip):
        st.session_state.dark_mode = not dark; st.rerun()
with ba2:
    db_label = "🗄️" if st.session_state.aba != "banco" else "📊"
    db_tip   = "Banco de Dados" if st.session_state.aba != "banco" else "Voltar ao Dashboard"
    if st.button(db_label, key="db_btn", help=db_tip):
        st.session_state.aba = "banco" if st.session_state.aba != "banco" else "dashboard"
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
            st.markdown(f"<hr>", unsafe_allow_html=True)
        st.info(f"Total: **{len(uploads)}** planilha(s) no banco.")
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
