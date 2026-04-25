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
    initial_sidebar_state="collapsed"
)

# ─── SUPABASE ────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = get_supabase()

# ─── CONSTANTES ──────────────────────────────────────────────────────────────
MESES    = ['JANEIRO','FEVEREIRO','MARÇO','ABRIL','MAIO','JUNHO',
            'JULHO','AGOSTO','SETEMBRO','OUTUBRO','NOVEMBRO','DEZEMBRO']
MESES_SH = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()

def fmt_brl(v):
    if not v or v == 0: return "—"
    return f"R$ {v:,.2f}".replace(",","X").replace(".",",").replace("X",".")

def fmt_short(v):
    if not v or v == 0: return "—"
    if v >= 1e6: return f"R${v/1e6:.1f}M"
    if v >= 1e3: return f"R${v/1e3:.0f}K"
    return fmt_brl(v)

# ─── AUTH ────────────────────────────────────────────────────────────────────
def do_login(login_str, senha):
    try:
        res = supabase.table("users").select("*")\
            .eq("login", login_str.lower().strip())\
            .eq("senha_hash", hash_senha(senha)).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        st.error(f"Erro ao conectar: {e}")
    return None

@st.cache_data(ttl=30)
def get_users():
    try:
        return supabase.table("users").select("id,login,nome,role").execute().data or []
    except: return []

def add_user(login_str, senha, nome, role="user"):
    try:
        supabase.table("users").insert({
            "login": login_str.lower().strip(),
            "senha_hash": hash_senha(senha),
            "nome": nome or login_str, "role": role
        }).execute()
        get_users.clear()
        return True
    except Exception as e:
        st.error(str(e)); return False

def delete_user(uid):
    try:
        supabase.table("users").delete().eq("id", uid).execute()
        get_users.clear(); return True
    except: return False

# ─── UPLOADS ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def get_uploads():
    try:
        return supabase.table("uploads")\
            .select("id,nome_arquivo,ano,criado_em,usuario_id")\
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
        st.error(f"Erro ao salvar: {e}"); return None

@st.cache_data(ttl=300)
def load_upload(upload_id):
    try:
        res = supabase.table("uploads").select("dados,nome_arquivo,ano").eq("id", upload_id).execute()
        if res.data:
            r = res.data[0]
            return json.loads(r["dados"]), r["nome_arquivo"], r["ano"]
    except Exception as e:
        st.error(f"Erro ao carregar: {e}")
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
    raw = list(raw_tuple)
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

# ─── TEMAS ───────────────────────────────────────────────────────────────────
DARK = {
    "bg": "#0d0500", "bg2": "#1a0a00", "bg3": "#2A0E00",
    "border": "#5A2000", "text": "#F0F0F0", "text2": "#CC8855",
    "card": "#1a0a00", "input_bg": "#2A0E00", "input_border": "#7A3500",
    "sidebar_bg": "linear-gradient(180deg,#1a0a00,#0d0500)",
}
LIGHT = {
    "bg": "#FFFFFF", "bg2": "#FFFFFF", "bg3": "#FFF8F4",
    "border": "#FFD5B8", "text": "#111111", "text2": "#C84E00",
    "card": "#FFFFFF", "input_bg": "#FFF8F4", "input_border": "#FFB380",
    "sidebar_bg": "linear-gradient(180deg,#1a0a00,#3d1500)",
}

def get_theme():
    return DARK if st.session_state.get("dark_mode", True) else LIGHT

def inject_css(T):
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
html, body, [class*="css"], .stApp {{
  font-family: 'Sora', sans-serif !important;
  background-color: {T['bg']} !important;
  color: {T['text']} !important;
}}
.block-container {{ padding: 0 1rem 2rem !important; max-width: 100% !important; }}
header[data-testid="stHeader"] {{ display: none !important; }}
#MainMenu, footer {{ display: none !important; }}
section[data-testid="stSidebar"] {{
  background: {T['sidebar_bg']} !important;
  border-right: 2px solid #F26522 !important;
}}
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label {{ color: {T['text']} !important; }}
div[data-baseweb="input"] input,
div[data-baseweb="select"] div,
div[data-baseweb="textarea"] textarea {{
  background: {T['input_bg']} !important;
  color: {T['text']} !important;
  border-color: {T['input_border']} !important;
}}
.stButton > button {{
  background: #F26522 !important; color: white !important;
  border: none !important; border-radius: 8px !important;
  font-family: 'Sora', sans-serif !important; font-weight: 600 !important;
  transition: background .15s !important;
}}
.stButton > button:hover {{ background: #C84E00 !important; }}
details {{ background: {T['bg3']} !important; border: 1px solid {T['border']} !important; border-radius: 10px !important; }}
details summary {{ color: {T['text']} !important; }}
div[data-testid="stFileUploader"] {{ background: {T['bg3']} !important; border: 2px dashed #F26522 !important; border-radius: 10px !important; }}
hr {{ border-color: {T['border']} !important; }}
.kpi-card {{
  background: {T['card']}; border: 1.5px solid {T['border']}; border-radius: 14px;
  padding: 18px 20px; position: relative; overflow: hidden;
  box-shadow: 0 4px 16px rgba(0,0,0,.15);
}}
.kpi-card::before {{ content: ''; position: absolute; top:0;left:0;right:0; height:3px; background:#F26522; }}
.kpi-card.hl {{ background: #F26522; border-color: #F26522; }}
.kpi-card.hl::before {{ background: rgba(255,255,255,.3); }}
.kpi-lbl {{ font-size:10px;font-weight:700;color:{T['text2']};text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px; }}
.kpi-card.hl .kpi-lbl {{ color: rgba(255,255,255,.8); }}
.kpi-val {{ font-size:22px;font-weight:700;letter-spacing:-.5px;color:{T['text']}; }}
.kpi-card.hl .kpi-val {{ color: white; }}
.kpi-sub {{ font-size:11px;color:{T['text2']};margin-top:3px; }}
.kpi-card.hl .kpi-sub {{ color: rgba(255,255,255,.7); }}
label[data-testid="stWidgetLabel"] p {{ color: {T['text2']} !important; font-size:11px !important; font-weight:700 !important; text-transform:uppercase !important; letter-spacing:.5px !important; }}
div[data-testid="collapsedControl"] {{ background: #F26522 !important; border-radius: 0 8px 8px 0 !important; }}
div[data-testid="collapsedControl"] svg {{ fill: white !important; }}
section[data-testid="stSidebar"] .stButton > button {{
  background: rgba(242,101,34,.15) !important;
  border: 1px solid #F26522 !important;
  color: #F26522 !important;
}}
section[data-testid="stSidebar"] .stButton > button:hover {{
  background: #F26522 !important; color: white !important;
}}
</style>
""", unsafe_allow_html=True)

# ─── SESSION ─────────────────────────────────────────────────────────────────
for k, v in [("user",None),("dados",[]),("ano","2025"),("arquivo",None),
             ("dark_mode",True),("aba","dashboard")]:
    if k not in st.session_state:
        st.session_state[k] = v

# ─── LOGIN ───────────────────────────────────────────────────────────────────
if st.session_state.user is None:
    T = get_theme()
    inject_css(T)
    st.markdown(f"""
    <style>
    .stApp {{ background: linear-gradient(135deg,#1a0a00 0%,#3d1500 50%,#F26522 100%) !important; }}
    div[data-testid="stForm"] {{
      background: white !important; border-radius: 20px !important;
      padding: 10px 20px 20px !important; border: none !important;
    }}
    div[data-testid="stForm"] input {{ background:#F7F7F7 !important;color:#111 !important;border:1.5px solid #E0E0E0 !important;border-radius:8px !important; }}
    div[data-testid="stForm"] label p {{ color:#666 !important;font-size:11px !important;font-weight:700 !important;text-transform:uppercase !important; }}
    div[data-testid="stForm"] button {{ background:#F26522 !important;font-size:15px !important;font-weight:700 !important;padding:12px !important;border-radius:8px !important; }}
    </style>
    """, unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.1, 1])
    with col2:
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
            st.markdown('<p style="font-size:13px;color:#666;margin-bottom:4px;text-align:center;">Acesse com seu login e senha</p>', unsafe_allow_html=True)
            login_input = st.text_input("Login", placeholder="Ex: joao.silva")
            senha_input = st.text_input("Senha", type="password", placeholder="••••••••")
            if st.form_submit_button("Entrar", use_container_width=True):
                if login_input and senha_input:
                    u = do_login(login_input, senha_input)
                    if u:
                        st.session_state.user = u; st.rerun()
                    else:
                        st.error("Usuário ou senha incorretos.")
                else:
                    st.warning("Preencha login e senha.")
        st.markdown('<p style="text-align:center;font-size:11px;color:rgba(255,255,255,.5);margin-top:20px;letter-spacing:.5px;">ASSOCIAÇÃO DO ENSINO SUPERIOR DA VITÓRIA STO ANTÃO</p>', unsafe_allow_html=True)
    st.stop()

# ─── APP ─────────────────────────────────────────────────────────────────────
T = get_theme()
inject_css(T)

user     = st.session_state.user
is_admin = user.get("role") == "admin"
nome     = user.get("nome") or user["login"]
iniciais = nome[:2].upper()
dark     = st.session_state.dark_mode
tema_ico = "☀️" if dark else "🌙"

# ── TOPBAR ───────────────────────────────────────────────────────────────────
# TOPBAR — botões reais do Streamlit estilizados como topbar
st.markdown("""
<style>
/* Topbar container */
div[data-testid="stHorizontalBlock"]:first-of-type {
  background: linear-gradient(90deg,#1a0a00,#3d1500) !important;
  border-bottom: 2.5px solid #F26522 !important;
  padding: 8px 20px !important;
  margin: -0.5rem -1rem 0.8rem -1rem !important;
  box-shadow: 0 4px 20px rgba(242,101,34,.2) !important;
  align-items: center !important;
}
/* Todos os botões na topbar */
div[data-testid="stHorizontalBlock"]:first-of-type .stButton > button {
  background: rgba(255,255,255,.08) !important;
  border: 1px solid rgba(255,255,255,.2) !important;
  color: white !important;
  border-radius: 10px !important;
  font-size: 16px !important;
  padding: 6px 12px !important;
  height: 38px !important;
  min-width: 42px !important;
  transition: all .2s !important;
}
div[data-testid="stHorizontalBlock"]:first-of-type .stButton > button:hover {
  background: #F26522 !important;
  border-color: #F26522 !important;
  transform: scale(1.05) !important;
}
</style>
""", unsafe_allow_html=True)

tb_menu, tb_brand, tb_esp, tb_tema, tb_db, tb_user = st.columns([0.4, 3.5, 2, 0.4, 0.4, 1.2])

with tb_menu:
    if st.button("☰", key="menu_btn", help="Painel lateral"):
        st.session_state.sidebar_open = not st.session_state.get("sidebar_open", False)
        st.rerun()

with tb_brand:
    aba_ativa = st.session_state.aba
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:12px;height:38px;">
      <div style="width:34px;height:34px;background:#F26522;border-radius:9px;
                  display:flex;align-items:center;justify-content:center;
                  font-size:14px;font-weight:800;color:white;flex-shrink:0;">UV</div>
      <span style="font-size:16px;font-weight:700;color:white;letter-spacing:-.3px;">
        UNIVISA <span style="color:#FF8C42;">Receitas</span>
      </span>
      <span style="background:rgba(242,101,34,.25);color:#FF8C42;font-size:10px;font-weight:700;
                   padding:3px 10px;border-radius:20px;border:1px solid rgba(242,101,34,.4);">
        {st.session_state.ano}
      </span>
      {"<span style='background:#F26522;color:white;font-size:10px;font-weight:700;padding:3px 10px;border-radius:20px;margin-left:4px;'>🗄 Banco de Dados</span>" if aba_ativa=="banco" else ""}
    </div>
    """, unsafe_allow_html=True)

with tb_tema:
    tema_label = "☀️" if dark else "🌙"
    tema_tip = "Modo claro" if dark else "Modo escuro"
    if st.button(tema_label, key="tema_btn", help=tema_tip):
        st.session_state.dark_mode = not dark
        st.rerun()

with tb_db:
    db_ativo = st.session_state.aba == "banco"
    if st.button("🗄", key="db_btn", help="Banco de Dados — Planilhas salvas"):
        st.session_state.aba = "banco" if not db_ativo else "dashboard"
        st.rerun()

with tb_user:
    st.markdown(f"""
    <div style="display:flex;align-items:center;justify-content:flex-end;gap:8px;height:38px;">
      <div style="background:#F26522;width:30px;height:30px;border-radius:50%;
                  display:flex;align-items:center;justify-content:center;
                  font-size:12px;font-weight:700;color:white;flex-shrink:0;">{iniciais}</div>
      <span style="font-size:12px;font-weight:600;color:white;white-space:nowrap;">{nome}</span>
    </div>
    """, unsafe_allow_html=True)

# ── SIDEBAR ──────────────────────────────────────────────────────────────────
if st.session_state.get("sidebar_open", False):
    with st.sidebar:
        st.markdown(f"""
        <div style="padding:14px 0 18px;border-bottom:1px solid rgba(242,101,34,.3);margin-bottom:14px;">
          <div style="display:flex;align-items:center;gap:10px;">
            <div style="width:38px;height:38px;background:#F26522;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:800;color:white;">{iniciais}</div>
            <div>
              <div style="font-size:13px;font-weight:700;color:{T['text']};">{nome}</div>
              <div style="font-size:10px;color:#F26522;text-transform:uppercase;letter-spacing:.5px;">{"Admin" if is_admin else "Usuário"}</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        if st.button("🚪 Sair", use_container_width=True, key="sair_btn"):
            st.session_state.user = None
            st.session_state.dados = []
            st.rerun()

        st.markdown(f'<div style="font-size:10px;font-weight:700;color:#F26522;text-transform:uppercase;letter-spacing:.8px;margin:18px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(242,101,34,.2);">📂 Planilhas Salvas</div>', unsafe_allow_html=True)

        uploads = get_uploads()
        if uploads:
            for up in uploads:
                col_up, col_del = st.columns([3,1])
                with col_up:
                    if st.button(f"📊 {up['ano']} — {up['nome_arquivo'][:16]}", key=f"load_{up['id']}", use_container_width=True):
                        d, arq, ano = load_upload(up["id"])
                        if d:
                            st.session_state.dados = d
                            st.session_state.arquivo = arq
                            st.session_state.ano = ano or "2025"
                            st.rerun()
                if is_admin:
                    with col_del:
                        if st.button("🗑", key=f"del_{up['id']}"):
                            delete_upload(up["id"]); st.rerun()
        else:
            st.markdown(f'<p style="font-size:12px;color:{T["text2"]};">Nenhuma salva ainda.</p>', unsafe_allow_html=True)

        if is_admin:
            st.markdown(f'<div style="font-size:10px;font-weight:700;color:#F26522;text-transform:uppercase;letter-spacing:.8px;margin:18px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(242,101,34,.2);">👤 Usuários</div>', unsafe_allow_html=True)
            with st.expander("➕ Adicionar"):
                with st.form("add_user_form"):
                    nu_login = st.text_input("Login")
                    nu_senha = st.text_input("Senha", type="password")
                    nu_nome  = st.text_input("Nome")
                    nu_role  = st.selectbox("Perfil", ["user","admin"])
                    if st.form_submit_button("Adicionar", use_container_width=True):
                        if nu_login and nu_senha:
                            if add_user(nu_login, nu_senha, nu_nome, nu_role):
                                st.success(f"'{nu_login}' adicionado!")
                        else:
                            st.error("Login e senha obrigatórios.")
            for u in get_users():
                c_u, c_d = st.columns([3,1])
                with c_u:
                    st.markdown(f'<span style="font-size:12px;color:{T["text"]};font-weight:600;">{u["nome"]}</span><br><span style="font-size:10px;color:#F26522;">{"🟠 Admin" if u["role"]=="admin" else "⚪ User"}</span>', unsafe_allow_html=True)
                with c_d:
                    if u["role"] != "admin" and st.button("✕", key=f"du_{u['id']}"):
                        delete_user(u["id"]); st.rerun()

# ─── ABA BANCO DE DADOS ──────────────────────────────────────────────────────
if st.session_state.aba == "banco":
    st.markdown(f'<h3 style="color:{T["text"]};margin-bottom:16px;">🗄️ Banco de Dados — Planilhas Salvas</h3>', unsafe_allow_html=True)
    uploads = get_uploads()
    if not uploads:
        st.info("Nenhuma planilha salva no banco ainda.")
    else:
        for up in uploads:
            col1, col2, col3, col4 = st.columns([3, 1.5, 1.5, 1])
            with col1:
                st.markdown(f'<span style="font-size:13px;font-weight:600;color:{T["text"]};">📊 {up["nome_arquivo"]}</span>', unsafe_allow_html=True)
            with col2:
                st.markdown(f'<span style="font-size:12px;color:{T["text2"]};">Ano: {up["ano"] or "—"}</span>', unsafe_allow_html=True)
            with col3:
                data = up.get("criado_em","")[:10] if up.get("criado_em") else "—"
                st.markdown(f'<span style="font-size:12px;color:{T["text2"]};">{data}</span>', unsafe_allow_html=True)
            with col4:
                if is_admin and st.button("🗑 Excluir", key=f"dbdel_{up['id']}"):
                    delete_upload(up["id"]); st.rerun()
            st.markdown(f"<hr style='margin:6px 0;border-color:{T['border']};'>", unsafe_allow_html=True)

        if is_admin:
            st.markdown("<br>", unsafe_allow_html=True)
            st.info(f"Total: **{len(uploads)}** planilha(s) salva(s) no banco.")
    st.stop()

# ─── UPLOAD ──────────────────────────────────────────────────────────────────
with st.expander("📁 Carregar nova planilha .xlsx", expanded=not bool(st.session_state.dados)):
    uploaded = st.file_uploader(
        "Arraste ou clique — formato Relatório de Receitas Líquidas UNIVISA",
        type=["xlsx","xls"], label_visibility="visible"
    )
    if uploaded:
        import openpyxl
        wb = openpyxl.load_workbook(uploaded, data_only=True)
        ws = wb.active
        raw = tuple(tuple(cell.value for cell in row) for row in ws.iter_rows())
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
            st.error("Não foi possível interpretar. Verifique o formato UNIVISA.")

# ─── FILTROS ─────────────────────────────────────────────────────────────────
dados = st.session_state.dados
if not dados:
    st.markdown(f"""
    <div style="text-align:center;padding:80px 20px;color:{T['text2']};">
      <div style="font-size:52px;margin-bottom:12px;opacity:.4;">📊</div>
      <p style="font-size:16px;font-weight:600;color:{T['text']};">Carregue uma planilha para visualizar os dados</p>
      <p style="font-size:13px;">Use o painel acima ou selecione uma planilha salva na barra lateral (☰)</p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

cats  = sorted(set(r["nome"] for r in dados if r["nivel"]==0))
areas = sorted(set(r["nome"] for r in dados if r["nivel"]==1))

fc1, fc2, fc3, fc4 = st.columns([2,2,2,3])
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

# ─── KPIs ────────────────────────────────────────────────────────────────────
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
    (k2,"Maior Receita",fmt_short(gv(maior)) if maior else "—",(maior["nome"][:28] if maior else "—"),False),
    (k3,"Qtd. Cursos",str(qtd),"com receita",False),
    (k4,"Pós-Graduação",fmt_short(pos_t),"total pós",False),
    (k5,"Média por Curso",fmt_short(media),"receita média",False),
]:
    with col:
        st.markdown(f"""
        <div class="kpi-card {'hl' if hl else ''}">
          <div class="kpi-lbl">{lbl}</div>
          <div class="kpi-val">{val}</div>
          <div class="kpi-sub">{sub}</div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ─── GRÁFICOS ────────────────────────────────────────────────────────────────
bg_chart = T["bg2"]
g1, g2 = st.columns(2)

with g1:
    st.markdown(f'<span style="font-size:13px;font-weight:700;color:{T["text"]};">Top 10 Cursos por Receita</span>', unsafe_allow_html=True)
    top10 = sorted(
        [(r["nome"].replace("Bacharelado em ","").replace("Licenciatura em ","").replace("Tecnólogo em ","")[:35], gv(r))
         for r in cursos if gv(r)>0], key=lambda x:x[1], reverse=True)[:10]
    if top10:
        df_top = pd.DataFrame(top10, columns=["Curso","Receita"])
        colors = [f"rgba(242,101,34,{1-i*0.08})" for i in range(len(df_top))]
        fig = go.Figure(go.Bar(
            x=df_top["Receita"], y=df_top["Curso"],
            orientation="h", marker_color=colors,
            marker_line_width=0
        ))
        fig.update_layout(
            showlegend=False, height=280, margin=dict(l=0,r=0,t=0,b=0),
            plot_bgcolor=bg_chart, paper_bgcolor=bg_chart, font_family="Sora",
            yaxis=dict(autorange="reversed", tickfont=dict(size=10,color=T["text2"])),
            xaxis=dict(tickfont=dict(size=10,color=T["text2"]), gridcolor=T["border"])
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

with g2:
    st.markdown(f'<span style="font-size:13px;font-weight:700;color:{T["text"]};">Distribuição por Área</span>', unsafe_allow_html=True)
    by_area = {}
    for r in cursos:
        if r.get("area") and gv(r)>0:
            by_area[r["area"]] = by_area.get(r["area"],0)+gv(r)
    if by_area:
        pal = ["#F26522","#FF8C42","#C84E00","#FFB380","#E05A00","#FFD5B8","#A03C00","#FFC4A0"]
        fig2 = go.Figure(go.Pie(
            labels=list(by_area.keys()), values=list(by_area.values()),
            hole=.6, marker_colors=pal[:len(by_area)],
            textfont_size=10,
        ))
        fig2.update_layout(
            height=280, margin=dict(l=0,r=0,t=0,b=0),
            legend=dict(font=dict(size=10,color=T["text2"]), orientation="v"),
            paper_bgcolor=bg_chart, font_family="Sora"
        )
        st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar":False})

# EVOLUÇÃO MENSAL
st.markdown(f'<span style="font-size:13px;font-weight:700;color:{T["text"]};">Evolução Mensal das Receitas</span>', unsafe_allow_html=True)
vals_mes = [sum(r["meses"].get(m,0) for r in cursos) for m in MESES]
fig3 = go.Figure(go.Scatter(
    x=MESES_SH, y=vals_mes, mode="lines+markers",
    line=dict(color="#F26522",width=2.5),
    fill="tozeroy", fillcolor="rgba(242,101,34,.08)",
    marker=dict(color="#F26522",size=6)
))
fig3.update_layout(
    height=160, margin=dict(l=0,r=0,t=0,b=0),
    plot_bgcolor=bg_chart, paper_bgcolor=bg_chart, font_family="Sora",
    xaxis=dict(gridcolor=T["border"], tickfont=dict(size=11,color=T["text2"])),
    yaxis=dict(gridcolor=T["border"], tickfont=dict(size=10,color=T["text2"])),
    showlegend=False
)
st.plotly_chart(fig3, use_container_width=True, config={"displayModeBar":False})

# TABELA
st.markdown(f'<span style="font-size:13px;font-weight:700;color:{T["text"]};">Tabela de Receitas</span>', unsafe_allow_html=True)
if mes_sel:
    df_show = pd.DataFrame([
        {"Centro de Custo / Curso": r["nome"],
         mes_sel.capitalize(): fmt_brl(r["meses"].get(mes_sel,0)) if r["meses"].get(mes_sel,0) else "—"}
        for r in filtered
    ])
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

# STATUSBAR
st.markdown(f"""
<div style="padding:8px 0;font-size:11px;color:{T['text2']};display:flex;gap:22px;flex-wrap:wrap;margin-top:6px;">
  <span><strong style="color:{T['text']}">Arquivo:</strong> {st.session_state.arquivo or 'Nenhum'}</span>
  <span><strong style="color:{T['text']}">Registros:</strong> {len(dados)}</span>
  <span><strong style="color:{T['text']}">Usuário:</strong> {nome}</span>
  <span><strong style="color:{T['text']}">Atualizado:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</span>
</div>
""", unsafe_allow_html=True)
