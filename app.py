import streamlit as st
import pandas as pd
import json
import hashlib
from supabase import create_client, Client
from datetime import datetime
import plotly.graph_objects as go
import io as _io
import json as _json

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

MESES    = ['JANEIRO','FEVEREIRO','MARÇO','ABRIL','MAIO','JUNHO',
            'JULHO','AGOSTO','SETEMBRO','OUTUBRO','NOVEMBRO','DEZEMBRO']
MESES_SH = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
MES_MAP  = {1:'JANEIRO',2:'FEVEREIRO',3:'MARÇO',4:'ABRIL',5:'MAIO',6:'JUNHO',
            7:'JULHO',8:'AGOSTO',9:'SETEMBRO',10:'OUTUBRO',11:'NOVEMBRO',12:'DEZEMBRO'}

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()

def fmt_brl(v):
    if not v or v == 0: return "—"
    try:
        v = float(v)
        s = f"{int(v):,}".replace(",",".")
        c = round((v - int(v)) * 100)
        return f"R$ {s},{c:02d}"
    except: return "—"

def fmt_short(v):
    if not v or v == 0: return "—"
    try:
        v = float(v)
        if v >= 1e6: return f"R${v/1e6:.1f}M"
        if v >= 1e3: return f"R${v/1e3:.0f}K"
        return fmt_brl(v)
    except: return "—"

# Classify centro de custo
def classify_curso(nome):
    n = str(nome).upper().strip()
    if 'CAMB' in n: return 'CAMB'
    if 'PÓS' in n or 'POS ' in n: return 'PÓS-GRADUAÇÃO'
    if n in ['GERAL','AESVISA','CLÍNICA DE SAÚDE ACOLHE','CLÍNICA']: return 'OUTROS'
    return 'GRADUAÇÃO'

def classify_tipo(tipo):
    t = str(tipo).upper().strip()
    if 'MENSALIDADE' in t or 'MENSALIDADES' in t: return 'Mensalidades'
    if 'TAXA' in t: return 'Taxas'
    return 'Outras Receitas'

# ─── DB ──────────────────────────────────────────────────────────────────────
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

def get_uploads(usuario_id=None, is_admin=False):
    try:
        query = supabase.table("uploads").select("id,nome_arquivo,ano,criado_em,usuario_id")
        if not is_admin and usuario_id:
            query = query.eq("usuario_id", usuario_id)
        return query.order("criado_em", desc=True).execute().data or []
    except: return []

def save_upload(nome, ano, dados, uid):
    try:
        res = supabase.table("uploads").insert({
            "nome_arquivo": nome, "ano": str(ano),
            "dados": json.dumps(dados, default=str), "usuario_id": uid,
            "criado_em": datetime.now().isoformat()
        }).execute()
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
        return True
    except: return False

def save_comparativo(nome, ano_a, ano_b, resultado, uid):
    try:
        res = supabase.table("comparativos").insert({
            "nome": nome,
            "ano_a": str(ano_a),
            "ano_b": str(ano_b),
            "resultado": json.dumps(resultado, default=str),
            "usuario_id": uid,
            "criado_em": datetime.now().isoformat()
        }).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        st.error(f"Erro ao salvar comparativo: {e}"); return None

def get_comparativos(usuario_id=None, is_admin=False):
    try:
        query = supabase.table("comparativos").select("id,nome,ano_a,ano_b,criado_em,usuario_id")
        if not is_admin and usuario_id:
            query = query.eq("usuario_id", usuario_id)
        return query.order("criado_em", desc=True).execute().data or []
    except: return []

def load_comparativo(comp_id):
    try:
        res = supabase.table("comparativos").select("*").eq("id", comp_id).execute()
        if res.data:
            r = res.data[0]
            return json.loads(r["resultado"]), r["nome"], r["ano_a"], r["ano_b"]
    except Exception as e:
        st.error(f"Erro ao carregar: {e}")
    return None, None, None, None

def delete_comparativo(comp_id):
    try:
        supabase.table("comparativos").delete().eq("id", comp_id).execute()
        return True
    except: return False

# ─── PARSER NOVO (BASE RAZÃO) ────────────────────────────────────────────────
@st.cache_data
def parse_base_razao(file_bytes, filename):
    """Parse planilha BASE RAZÃO — formato transacional diário."""
    try:
        buf = _io.BytesIO(file_bytes)
        # Detect engine by filename extension
        if filename.lower().endswith('.xls'):
            engine = 'xlrd'
        else:
            engine = 'openpyxl'
        
        xls = pd.ExcelFile(buf, engine=engine)
        if 'BASE RAZÃO' in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name='BASE RAZÃO', engine=engine)
        else:
            df = pd.read_excel(xls, sheet_name=0, engine=engine)

        df.columns = [str(c).strip().upper() for c in df.columns]
        # Normalize columns
        col_map = {}
        for c in df.columns:
            if 'DATA' in c: col_map[c] = 'DATA'
            elif 'TIPO' in c: col_map[c] = 'TIPO'
            elif 'CENTRO' in c and 'COD' not in c and 'CÓD' not in c: col_map[c] = 'CENTRO'
            elif 'VALOR' in c: col_map[c] = 'VALOR'
        df = df.rename(columns=col_map)

        if 'DATA' not in df.columns or 'VALOR' not in df.columns:
            return None, None

        df['DATA']   = pd.to_datetime(df['DATA'], errors='coerce')
        df['VALOR']  = pd.to_numeric(df['VALOR'], errors='coerce').fillna(0)
        df['MES']    = df['DATA'].dt.month
        df['ANO']    = df['DATA'].dt.year
        df['CENTRO'] = df['CENTRO'].fillna('GERAL').str.strip()
        df['TIPO']   = df['TIPO'].fillna('Mensalidades').apply(classify_tipo)
        df['CATEGORIA'] = df['CENTRO'].apply(classify_curso)

        anos = sorted(df['ANO'].dropna().unique().astype(int).tolist())

        # Build records
        records = []
        for (centro, tipo, cat, ano, mes), grp in df.groupby(['CENTRO','TIPO','CATEGORIA','ANO','MES']):
            records.append({
                'centro': centro,
                'tipo': tipo,
                'categoria': cat,
                'ano': int(ano),
                'mes': int(mes),
                'mes_nome': MES_MAP.get(int(mes), ''),
                'valor': float(grp['VALOR'].sum())
            })

        return records, anos
    except Exception as e:
        st.error(f"Erro ao processar: {e}")
        return None, None

# ─── SESSION ─────────────────────────────────────────────────────────────────
for k, v in [("user",None),("df_records",[]),("anos_disponiveis",[]),
             ("arquivo",None),("dark_mode",False),("aba","dashboard"),
             ("last_file",None),("comp_salvo",None),("comp_nome_salvo","")]:
    if k not in st.session_state:
        st.session_state[k] = v

dark = st.session_state.dark_mode
BG    = "#0d0500" if dark else "#FFFFFF"
BG2   = "#1a0a00" if dark else "#FFFFFF"
BG3   = "#2A0E00" if dark else "#FFF8F4"
BORD  = "#7A3500" if dark else "#FFD5B8"
TEXT  = "#F0F0F0" if dark else "#111111"
TEXT2 = "#FF8C42" if dark else "#C84E00"
CARD  = "#1a0a00" if dark else "#FFFFFF"
INP   = "#2A0E00" if dark else "#FFF8F4"
INPB  = "#7A3500" if dark else "#FFB380"
CHART = "#1a0a00" if dark else "#FFFFFF"
GRID  = "#5A2000" if dark else "#FFE5D0"

# ─── CSS ─────────────────────────────────────────────────────────────────────
LOGO_B64 = "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"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700;800&display=swap');
html,body,[class*="css"],.stApp{{font-family:'Sora',sans-serif!important;background-color:{BG}!important;color:{TEXT}!important;}}
.block-container{{padding:0 1.2rem 2rem!important;max-width:100%!important;}}
header[data-testid="stHeader"]{{display:none!important;}}#MainMenu,footer{{display:none!important;}}
section[data-testid="stSidebar"]{{background:linear-gradient(180deg,#1a0a00,#0d0500)!important;border-right:2.5px solid #F26522!important;}}
section[data-testid="stSidebar"] p,section[data-testid="stSidebar"] span,section[data-testid="stSidebar"] label,section[data-testid="stSidebar"] div{{color:#FFD5B8!important;}}
div[data-baseweb="input"] input,div[data-baseweb="select"] div,div[data-baseweb="textarea"] textarea{{background:{INP}!important;color:{TEXT}!important;border-color:{INPB}!important;}}
.stButton>button{{background:rgba(242,101,34,.12)!important;color:#F26522!important;border:1.5px solid #F26522!important;border-radius:8px!important;font-family:'Sora',sans-serif!important;font-weight:600!important;transition:all .2s!important;}}
.stButton>button:hover{{background:#F26522!important;color:white!important;}}
/* Topbar action buttons - white style */
div[data-testid="stHorizontalBlock"] > div:nth-child(-n+5) .stButton>button{{background:rgba(255,255,255,.1)!important;color:white!important;border:1px solid rgba(255,255,255,.3)!important;font-size:15px!important;padding:4px 10px!important;}}
div[data-testid="stHorizontalBlock"] > div:nth-child(-n+5) .stButton>button:hover{{background:#F26522!important;border-color:#F26522!important;}}
details{{background:white!important;border:1px solid {BORD}!important;border-radius:10px!important;}}
details summary{{color:#111!important;font-weight:600!important;}}
details>div{{background:white!important;}}
div[data-testid="stFileUploader"]{{background:white!important;border:2px dashed #F26522!important;border-radius:10px!important;}}
div[data-testid="stFileUploader"] *{{color:#111!important;background:white!important;}}
div[data-testid="stFileUploader"] button{{background:#F26522!important;color:white!important;border:none!important;}}
hr{{border-color:{BORD}!important;margin:6px 0 12px!important;}}
div[data-testid="collapsedControl"]{{background:#F26522!important;border-radius:0 10px 10px 0!important;}}
div[data-testid="collapsedControl"] svg{{fill:white!important;}}
section[data-testid="stSidebar"] .stButton>button{{background:rgba(242,101,34,.15)!important;border:1px solid #F26522!important;color:#FF8C42!important;}}
section[data-testid="stSidebar"] .stButton>button:hover{{background:#F26522!important;color:white!important;}}
.kpi-card{{background:{CARD};border:1.5px solid {BORD};border-radius:14px;padding:18px 20px;position:relative;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,.1);margin-bottom:4px;}}
.kpi-card::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:#F26522;}}
.kpi-card.hl{{background:#F26522!important;border-color:#F26522!important;}}
.kpi-card.hl::before{{background:rgba(255,255,255,.3);}}
.kpi-lbl{{font-size:10px;font-weight:700;color:{TEXT2};text-transform:uppercase;letter-spacing:.5px;margin-bottom:7px;}}
.kpi-card.hl .kpi-lbl{{color:rgba(255,255,255,.8);}}
.kpi-val{{font-size:22px;font-weight:700;letter-spacing:-.5px;color:{TEXT};}}
.kpi-card.hl .kpi-val{{color:white;}}
.kpi-sub{{font-size:11px;color:{TEXT2};margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.kpi-card.hl .kpi-sub{{color:rgba(255,255,255,.7);}}
label[data-testid="stWidgetLabel"] p{{color:{TEXT2}!important;font-size:11px!important;font-weight:700!important;text-transform:uppercase!important;letter-spacing:.5px!important;}}
</style>
""", unsafe_allow_html=True)

# ─── LOGIN ───────────────────────────────────────────────────────────────────
if st.session_state.user is None:
    st.markdown("""
    <style>
    .stApp{background:linear-gradient(135deg,#1a0a00 0%,#3d1500 50%,#F26522 100%)!important;}
    div[data-testid="stForm"]{background:white!important;border-radius:20px!important;padding:10px 20px 20px!important;}
    div[data-testid="stForm"] input{background:#F7F7F7!important;color:#111!important;border:1.5px solid #E0E0E0!important;border-radius:8px!important;}
    div[data-testid="stForm"] label p{color:#888!important;font-size:11px!important;font-weight:700!important;text-transform:uppercase!important;}
    div[data-testid="stForm"] button{background:#F26522!important;color:white!important;font-size:15px!important;font-weight:700!important;border:none!important;border-radius:8px!important;}
    </style>
    """, unsafe_allow_html=True)
    _, col, _ = st.columns([1,1.1,1])
    with col:
        st.markdown(f"""
        <div style="margin-top:80px;margin-bottom:24px;text-align:center;">
          <div style="display:inline-flex;align-items:center;gap:14px;background:white;padding:20px 32px;border-radius:18px;box-shadow:0 8px 32px rgba(0,0,0,.25);">
            <img src="data:image/png;base64,{LOGO_B64}" style="height:52px;width:auto;">
            <div>
              <div style="font-size:22px;font-weight:800;color:#111;letter-spacing:-.5px;">UNIVISA <span style="color:#F26522">Receitas</span></div>
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
        <div style="width:38px;height:38px;background:#F26522;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:800;color:white;">{iniciais}</div>
        <div>
          <div style="font-size:13px;font-weight:700;color:white!important;">{nome}</div>
          <div style="font-size:10px;color:#F26522!important;text-transform:uppercase;letter-spacing:.5px;">{"Admin" if is_admin else "Usuário"}</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🚪 Sair", use_container_width=True):
        st.session_state.user = None; st.session_state.df_records = []; st.rerun()

    st.markdown('<div style="font-size:10px;font-weight:700;color:#F26522;text-transform:uppercase;letter-spacing:.8px;margin:18px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(242,101,34,.2);">📂 Planilhas Salvas</div>', unsafe_allow_html=True)
    uploads = get_uploads(usuario_id=user["id"], is_admin=is_admin)
    if uploads:
        for up in uploads:
            c1, c2 = st.columns([4,1])
            with c1:
                if st.button(f"📊 {up['ano']} — {up['nome_arquivo'][:16]}", key=f"sb_{up['id']}", use_container_width=True):
                    d, arq, ano = load_upload(up["id"])
                    if d:
                        st.session_state.df_records = d
                        st.session_state.arquivo = arq
                        st.session_state.anos_disponiveis = sorted(set(r['ano'] for r in d))
                        st.rerun()
            if is_admin:
                with c2:
                    if st.button("🗑", key=f"sbdel_{up['id']}"):
                        delete_upload(up["id"]); st.rerun()
    else:
        st.markdown('<p style="font-size:12px;color:#AA6644;">Nenhuma salva ainda.</p>', unsafe_allow_html=True)

    # Comparativos salvos na sidebar
    comps_sb = get_comparativos(usuario_id=user["id"], is_admin=is_admin)
    if comps_sb:
        st.markdown('<div style="font-size:10px;font-weight:700;color:#F26522;text-transform:uppercase;letter-spacing:.8px;margin:18px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(242,101,34,.2);">📊 Comparativos Salvos</div>', unsafe_allow_html=True)
        for cp_sb in comps_sb:
            if st.button(f"📊 {cp_sb['nome'][:22]}", key=f"sb_comp_{cp_sb['id']}", use_container_width=True):
                res_cp, nome_cp, ano_a_cp, ano_b_cp = load_comparativo(cp_sb["id"])
                if res_cp:
                    st.session_state.comp_salvo = res_cp
                    st.session_state.comp_nome_salvo = nome_cp
                    st.session_state.aba = "comparativo"
                    st.rerun()

    if is_admin:
        st.markdown('<div style="font-size:10px;font-weight:700;color:#F26522;text-transform:uppercase;letter-spacing:.8px;margin:18px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(242,101,34,.2);">👤 Usuários</div>', unsafe_allow_html=True)
        with st.expander("➕ Adicionar"):
            with st.form("add_user_sb"):
                nl = st.text_input("Login"); ns = st.text_input("Senha", type="password")
                nn = st.text_input("Nome"); nr = st.selectbox("Perfil", ["user","admin"])
                if st.form_submit_button("Adicionar", use_container_width=True):
                    if nl and ns:
                        if add_user(nl, ns, nn, nr): st.success("Adicionado!")
                    else: st.error("Login e senha obrigatórios.")
        for u in get_users():
            cu, cd = st.columns([3,1])
            with cu:
                st.markdown(f'<div style="font-size:12px;color:white;font-weight:600;">{u["nome"]}<br><span style="font-size:10px;color:#F26522;">{"🟠 Admin" if u["role"]=="admin" else "⚪ User"}</span></div>', unsafe_allow_html=True)
            with cd:
                if u["role"] != "admin" and st.button("✕", key=f"du_{u['id']}"):
                    delete_user(u["id"]); st.rerun()

# ── TOPBAR ───────────────────────────────────────────────────────────────────
tema_ico = "☀️" if dark else "🌙"
anos_disp = st.session_state.anos_disponiveis

st.markdown(f"""
<div style="background:linear-gradient(90deg,#1a0a00,#3d1500);border-bottom:2.5px solid #F26522;
            padding:0 20px;height:58px;display:flex;align-items:center;justify-content:space-between;
            margin:0 -1.2rem .8rem -1.2rem;box-shadow:0 4px 20px rgba(242,101,34,.2);">
  <div style="display:flex;align-items:center;gap:14px;">
    <div style="background:white;border-radius:8px;padding:3px 6px;">
      <img src="data:image/png;base64,{LOGO_B64}" style="height:38px;width:auto;">
    </div>
    <span style="font-size:17px;font-weight:700;color:white;letter-spacing:-.3px;">Receitas</span>
    {f'<span style="background:rgba(242,101,34,.25);color:#FF8C42;font-size:11px;font-weight:700;padding:3px 12px;border-radius:20px;border:1px solid rgba(242,101,34,.4);">{" | ".join(str(a) for a in anos_disp)}</span>' if anos_disp else ''}
  </div>
  <div style="display:flex;align-items:center;gap:10px;">
    <div style="background:#F26522;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:white;">{iniciais}</div>
    <span style="font-size:13px;font-weight:600;color:white;">{nome}</span>
  </div>
</div>
""", unsafe_allow_html=True)

ba1, ba2, ba3, ba4, ba5, _ = st.columns([0.18,0.18,0.22,0.18,0.18,4])
with ba1:
    if st.button(tema_ico, key="tema_btn", help="Alternar tema"):
        st.session_state.dark_mode = not dark; st.rerun()
with ba2:
    if st.button("🗄️", key="db_btn", help="Banco de Dados"):
        st.session_state.aba = "banco" if st.session_state.aba != "banco" else "dashboard"; st.rerun()
with ba3:
    if st.button("📊", key="comp_btn", help="Comparativo entre Anos"):
        st.session_state.aba = "comparativo" if st.session_state.aba != "comparativo" else "dashboard"; st.rerun()
with ba4:
    if is_admin and st.button("👤", key="usr_btn", help="Usuários"):
        st.session_state.aba = "usuarios" if st.session_state.aba != "usuarios" else "dashboard"; st.rerun()
with ba5:
    if st.button("🚪", key="sair_top", help="Sair"):
        st.session_state.user = None; st.session_state.df_records = []; st.rerun()


# ── ABA COMPARATIVO ───────────────────────────────────────────────────────────
if st.session_state.aba == "comparativo":
    st.markdown(f'<h3 style="color:{TEXT};margin:8px 0 4px;">📊 Comparativo entre Anos</h3>', unsafe_allow_html=True)
    st.markdown(f'<p style="color:{TEXT2};font-size:13px;margin-bottom:20px;">Anexe duas planilhas para comparar o desempenho ano a ano por categoria.</p>', unsafe_allow_html=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f'<div style="font-size:12px;font-weight:700;color:{TEXT2};text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;">📁 Planilha A (ex: 2024)</div>', unsafe_allow_html=True)
        file_a = st.file_uploader("Planilha A", type=["xls","xlsx"], key="comp_a", label_visibility="collapsed")
    with col_b:
        st.markdown(f'<div style="font-size:12px;font-weight:700;color:{TEXT2};text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;">📁 Planilha B (ex: 2025)</div>', unsafe_allow_html=True)
        file_b = st.file_uploader("Planilha B", type=["xls","xlsx"], key="comp_b", label_visibility="collapsed")

    # Show saved comparativo if loaded from sidebar
    if st.session_state.get("comp_salvo") and not (file_a and file_b):
        res_loaded = st.session_state.comp_salvo
        st.markdown(f'<div style="background:{BG3};border:1.5px solid {BORD};border-radius:12px;padding:14px 18px;margin-bottom:16px;display:flex;align-items:center;gap:10px;"><span style="font-size:20px;">📊</span><div><div style="font-weight:700;color:{TEXT};">{st.session_state.comp_nome_salvo}</div><div style="font-size:12px;color:{TEXT2};">Comparativo salvo · {res_loaded.get("ano_a")} vs {res_loaded.get("ano_b")} · Clique em Limpar para fazer novo</div></div></div>', unsafe_allow_html=True)
        c_limpar, _ = st.columns([1,5])
        with c_limpar:
            if st.button("🗑 Limpar", key="limpar_comp_salvo"):
                st.session_state.comp_salvo = None
                st.session_state.comp_nome_salvo = ""
                st.rerun()
        # Display saved results
        r = res_loaded
        tot_a_s = r.get("tot_a",0); tot_b_s = r.get("tot_b",0)
        ano_a_s = r.get("ano_a","A"); ano_b_s = r.get("ano_b","B")
        var_s = r.get("var_total",0); melhor_s = r.get("melhor_total","—")
        cat_data_s = r.get("cat_data",[])
        rows_tab_s = r.get("rows_tab",[])
        vm_a_s = r.get("vm_a",[]); vm_b_s = r.get("vm_b",[])

        k1s,k2s,k3s,k4s = st.columns(4)
        with k1s: st.markdown(f'<div class="kpi-card hl"><div class="kpi-lbl">Total {ano_a_s}</div><div class="kpi-val">{fmt_short(tot_a_s)}</div><div class="kpi-sub">{r.get("label_a","")}</div></div>', unsafe_allow_html=True)
        with k2s: st.markdown(f'<div class="kpi-card hl" style="background:#C84E00!important;border-color:#C84E00!important;"><div class="kpi-lbl">Total {ano_b_s}</div><div class="kpi-val">{fmt_short(tot_b_s)}</div><div class="kpi-sub">{r.get("label_b","")}</div></div>', unsafe_allow_html=True)
        with k3s:
            sinal_s = "▲" if var_s >= 0 else "▼"; cor_s = "#27AE60" if var_s >= 0 else "#E74C3C"
            st.markdown(f'<div class="kpi-card"><div class="kpi-lbl">Variação</div><div class="kpi-val" style="color:{cor_s};">{sinal_s} {abs(var_s):.1f}%</div><div class="kpi-sub">de {ano_a_s} para {ano_b_s}</div></div>', unsafe_allow_html=True)
        with k4s: st.markdown(f'<div class="kpi-card"><div class="kpi-lbl">Melhor Ano</div><div class="kpi-val">{melhor_s}</div><div class="kpi-sub">maior receita total</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        if rows_tab_s:
            st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:8px;">Tabela Consolidada</p>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(rows_tab_s), use_container_width=True, hide_index=True)

        if vm_a_s and vm_b_s:
            st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin:12px 0 4px;">Evolução Mensal — {ano_a_s} vs {ano_b_s}</p>', unsafe_allow_html=True)
            fig_ev_s = go.Figure()
            fig_ev_s.add_trace(go.Scatter(x=MESES_SH, y=vm_a_s, mode="lines+markers", name=ano_a_s,
                line=dict(color="#F26522",width=2.5), marker=dict(size=6)))
            fig_ev_s.add_trace(go.Scatter(x=MESES_SH, y=vm_b_s, mode="lines+markers", name=ano_b_s,
                line=dict(color="#1A7A3A",width=2.5), marker=dict(size=6)))
            fig_ev_s.update_layout(height=200, margin=dict(l=0,r=0,t=0,b=0),
                plot_bgcolor=CHART, paper_bgcolor=CHART, font_family="Sora",
                legend=dict(font=dict(size=10,color=TEXT2)),
                xaxis=dict(gridcolor=GRID, tickfont=dict(size=11,color=TEXT2)),
                yaxis=dict(gridcolor=GRID, tickfont=dict(size=10,color=TEXT2)))
            st.plotly_chart(fig_ev_s, use_container_width=True, config={"displayModeBar":False}, key="ev_salvo")
        st.stop()

    if file_a and file_b:
        with st.spinner("Processando planilhas..."):
            recs_a, anos_a = parse_base_razao(file_a.read(), file_a.name)
            recs_b, anos_b = parse_base_razao(file_b.read(), file_b.name)

        if not recs_a or not recs_b:
            st.error("Não foi possível processar uma das planilhas.")
            st.stop()

        df_a = pd.DataFrame(recs_a)
        df_b = pd.DataFrame(recs_b)
        label_a = file_a.name.replace(".xls","").replace(".xlsx","")[:20]
        label_b = file_b.name.replace(".xls","").replace(".xlsx","")[:20]
        ano_a = str(anos_a[-1] if anos_a else "A")
        ano_b = str(anos_b[-1] if anos_b else "B")

        CATS = ["Mensalidades Graduação","Mensalidades Pós-Graduação","Mensalidades CAMB","Mensalidades Taxas","Outras Receitas"]
        PAL  = {"Mensalidades Graduação":"#F26522","Mensalidades Pós-Graduação":"#FF8C42",
                "Mensalidades CAMB":"#C84E00","Mensalidades Taxas":"#A03C00","Outras Receitas":"#FFB380"}

        tot_a = df_a["valor"].sum()
        tot_b = df_b["valor"].sum()
        melhor_total = ano_a if tot_a >= tot_b else ano_b
        var_total = ((tot_b - tot_a) / tot_a * 100) if tot_a else 0

        # ── KPI GERAL ─────────────────────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        k1,k2,k3,k4 = st.columns(4)
        with k1:
            st.markdown(f'''<div class="kpi-card hl">
              <div class="kpi-lbl">Total {ano_a}</div>
              <div class="kpi-val">{fmt_short(tot_a)}</div>
              <div class="kpi-sub">{label_a}</div>
            </div>''', unsafe_allow_html=True)
        with k2:
            st.markdown(f'''<div class="kpi-card hl" style="background:#C84E00!important;border-color:#C84E00!important;">
              <div class="kpi-lbl">Total {ano_b}</div>
              <div class="kpi-val">{fmt_short(tot_b)}</div>
              <div class="kpi-sub">{label_b}</div>
            </div>''', unsafe_allow_html=True)
        with k3:
            sinal = "▲" if var_total >= 0 else "▼"
            cor_var = "#27AE60" if var_total >= 0 else "#E74C3C"
            st.markdown(f'''<div class="kpi-card">
              <div class="kpi-lbl">Variação</div>
              <div class="kpi-val" style="color:{cor_var};">{sinal} {abs(var_total):.1f}%</div>
              <div class="kpi-sub">de {ano_a} para {ano_b}</div>
            </div>''', unsafe_allow_html=True)
        with k4:
            st.markdown(f'''<div class="kpi-card">
              <div class="kpi-lbl">Melhor Ano</div>
              <div class="kpi-val">{melhor_total}</div>
              <div class="kpi-sub">maior receita total</div>
            </div>''', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── COMPARATIVO POR CATEGORIA ─────────────────────────────────────────
        st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:12px;">Comparativo por Categoria</p>', unsafe_allow_html=True)

        cat_data = []
        for cat in CATS:
            v_a = df_a[df_a["categoria"]==cat]["valor"].sum()
            v_b = df_b[df_b["categoria"]==cat]["valor"].sum()
            if v_a == 0 and v_b == 0: continue
            var = ((v_b-v_a)/v_a*100) if v_a else 0
            melhor = ano_a if v_a >= v_b else ano_b
            sinal = "▲" if var >= 0 else "▼"
            cor = "#27AE60" if var >= 0 else "#E74C3C"
            cat_data.append({"cat":cat,"v_a":v_a,"v_b":v_b,"var":var,"melhor":melhor,"sinal":sinal,"cor":cor})

        for cd in cat_data:
            c1,c2,c3,c4,c5 = st.columns([2.5,1.5,1.5,1.2,1])
            with c1:
                st.markdown(f'<span style="font-size:13px;font-weight:700;color:{TEXT};">● {cd["cat"]}</span>', unsafe_allow_html=True)
            with c2:
                st.markdown(f'<span style="font-size:12px;color:#F26522;font-weight:600;">{fmt_brl(cd["v_a"])}<br><span style="font-size:10px;color:{TEXT2};">{ano_a}</span></span>', unsafe_allow_html=True)
            with c3:
                st.markdown(f'<span style="font-size:12px;color:#C84E00;font-weight:600;">{fmt_brl(cd["v_b"])}<br><span style="font-size:10px;color:{TEXT2};">{ano_b}</span></span>', unsafe_allow_html=True)
            with c4:
                st.markdown(f'<span style="font-size:13px;font-weight:700;color:{cd["cor"]};">{cd["sinal"]} {abs(cd["var"]):.1f}%</span>', unsafe_allow_html=True)
            with c5:
                st.markdown(f'<span style="background:#F26522;color:white;font-size:10px;font-weight:700;padding:3px 8px;border-radius:12px;">🏆 {cd["melhor"]}</span>', unsafe_allow_html=True)
            st.markdown(f"<hr style='margin:6px 0;border-color:{BORD};'>", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── GRÁFICO BARRAS COMPARATIVO ────────────────────────────────────────
        g1, g2 = st.columns(2)
        with g1:
            st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Receita por Categoria — {ano_a} vs {ano_b}</p>', unsafe_allow_html=True)
            cats_plot   = [cd["cat"] for cd in cat_data]
            vals_a_plot = [cd["v_a"] for cd in cat_data]
            vals_b_plot = [cd["v_b"] for cd in cat_data]
            # Color each bar by which year won
            clrs_a = ["#F26522" if cd["v_a"]>=cd["v_b"] else "#FFCBA4" for cd in cat_data]
            clrs_b = ["#1A7A3A" if cd["v_b"]>cd["v_a"] else "#A8D5B5" for cd in cat_data]
            fig_bar_comp = go.Figure()
            fig_bar_comp.add_trace(go.Bar(
                name=f"🟠 {ano_a}", x=cats_plot, y=vals_a_plot,
                marker_color=clrs_a,
                customdata=[f"{fmt_brl(cd['v_a'])} {'🏆' if cd['v_a']>=cd['v_b'] else ''}" for cd in cat_data],
                hovertemplate="<b>%{x}</b><br>%{customdata}<extra>"+ano_a+"</extra>"
            ))
            fig_bar_comp.add_trace(go.Bar(
                name=f"🟢 {ano_b}", x=cats_plot, y=vals_b_plot,
                marker_color=clrs_b,
                customdata=[f"{fmt_brl(cd['v_b'])} {'🏆' if cd['v_b']>cd['v_a'] else ''}" for cd in cat_data],
                hovertemplate="<b>%{x}</b><br>%{customdata}<extra>"+ano_b+"</extra>"
            ))
            fig_bar_comp.update_layout(
                barmode="group", height=340, margin=dict(l=0,r=0,t=10,b=60),
                plot_bgcolor=CHART, paper_bgcolor=CHART, font_family="Sora",
                legend=dict(font=dict(size=11,color=TEXT2), orientation="h", y=-0.18),
                xaxis=dict(tickfont=dict(size=9,color=TEXT2), gridcolor=GRID, tickangle=-20),
                yaxis=dict(tickfont=dict(size=10,color=TEXT2), gridcolor=GRID),
                annotations=[
                    dict(x=i, y=max(cd["v_a"],cd["v_b"])*1.05,
                         text="🏆", showarrow=False, font=dict(size=14))
                    for i, cd in enumerate(cat_data)
                ]
            )
            st.plotly_chart(fig_bar_comp, use_container_width=True, config={"displayModeBar":False}, key="bar_comp")

        with g2:
            st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Evolução Mensal — {ano_a} vs {ano_b}</p>', unsafe_allow_html=True)
            vm_a = [df_a[df_a["mes"]==m]["valor"].sum() for m in range(1,13)]
            vm_b = [df_b[df_b["mes"]==m]["valor"].sum() for m in range(1,13)]
            fig_line_comp = go.Figure()
            fig_line_comp.add_trace(go.Scatter(x=MESES_SH, y=vm_a, mode="lines+markers",
                name=ano_a, line=dict(color="#F26522",width=2.5), marker=dict(size=6),
                customdata=[fmt_brl(v) for v in vm_a],
                hovertemplate="<b>%{x}</b><br>%{customdata}<extra>"+ano_a+"</extra>"))
            fig_line_comp.add_trace(go.Scatter(x=MESES_SH, y=vm_b, mode="lines+markers",
                name=ano_b, line=dict(color="#C84E00",width=2.5), marker=dict(size=6),
                customdata=[fmt_brl(v) for v in vm_b],
                hovertemplate="<b>%{x}</b><br>%{customdata}<extra>"+ano_b+"</extra>"))
            fig_line_comp.update_layout(
                height=320, margin=dict(l=0,r=0,t=0,b=0),
                plot_bgcolor=CHART, paper_bgcolor=CHART, font_family="Sora",
                legend=dict(font=dict(size=11,color=TEXT2)),
                xaxis=dict(gridcolor=GRID, tickfont=dict(size=11,color=TEXT2)),
                yaxis=dict(gridcolor=GRID, tickfont=dict(size=10,color=TEXT2))
            )
            st.plotly_chart(fig_line_comp, use_container_width=True, config={"displayModeBar":False}, key="line_comp")

        # ── PIZZA LADO A LADO ─────────────────────────────────────────────────
        p1, p2 = st.columns(2)
        with p1:
            st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Distribuição {ano_a}</p>', unsafe_allow_html=True)
            by_cat_a = df_a.groupby("categoria")["valor"].sum().reset_index()
            fp_a = go.Figure(go.Pie(labels=by_cat_a["categoria"], values=by_cat_a["valor"], hole=.5,
                marker_colors=[PAL.get(c,"#FFB380") for c in by_cat_a["categoria"]],
                hovertemplate="<b>%{label}</b><br>%{percent}<extra></extra>"))
            fp_a.update_layout(height=280, margin=dict(l=0,r=0,t=0,b=0),
                legend=dict(font=dict(size=9,color=TEXT2)), paper_bgcolor=CHART, font_family="Sora")
            st.plotly_chart(fp_a, use_container_width=True, config={"displayModeBar":False}, key="pie_comp_a")

        with p2:
            st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Distribuição {ano_b}</p>', unsafe_allow_html=True)
            by_cat_b = df_b.groupby("categoria")["valor"].sum().reset_index()
            fp_b = go.Figure(go.Pie(labels=by_cat_b["categoria"], values=by_cat_b["valor"], hole=.5,
                marker_colors=[PAL.get(c,"#FFB380") for c in by_cat_b["categoria"]],
                hovertemplate="<b>%{label}</b><br>%{percent}<extra></extra>"))
            fp_b.update_layout(height=280, margin=dict(l=0,r=0,t=0,b=0),
                legend=dict(font=dict(size=9,color=TEXT2)), paper_bgcolor=CHART, font_family="Sora")
            st.plotly_chart(fp_b, use_container_width=True, config={"displayModeBar":False}, key="pie_comp_b")

        # ── TABELA CONSOLIDADA ────────────────────────────────────────────────
        st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin:16px 0 8px;">Tabela Consolidada</p>', unsafe_allow_html=True)
        rows_tab = []
        for cat in CATS:
            v_a2 = df_a[df_a["categoria"]==cat]["valor"].sum()
            v_b2 = df_b[df_b["categoria"]==cat]["valor"].sum()
            if v_a2 == 0 and v_b2 == 0: continue
            var2 = ((v_b2-v_a2)/v_a2*100) if v_a2 else 0
            rows_tab.append({
                "Categoria": cat,
                f"{ano_a}": fmt_brl(v_a2),
                f"{ano_b}": fmt_brl(v_b2),
                "Variação": f"{'▲' if var2>=0 else '▼'} {abs(var2):.1f}%",
                "Melhor Ano": f"🏆 {ano_a if v_a2>=v_b2 else ano_b}"
            })
        rows_tab.append({
            "Categoria": "TOTAL GERAL",
            f"{ano_a}": fmt_brl(tot_a),
            f"{ano_b}": fmt_brl(tot_b),
            "Variação": f"{'▲' if var_total>=0 else '▼'} {abs(var_total):.1f}%",
            "Melhor Ano": f"🏆 {melhor_total}"
        })
        st.dataframe(pd.DataFrame(rows_tab), use_container_width=True, hide_index=True)

        # ── SALVAR COMPARATIVO ────────────────────────────────────────────────
        st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin:20px 0 8px;">💾 Salvar este Comparativo</p>', unsafe_allow_html=True)
        sv1, sv2 = st.columns([3,1])
        with sv1:
            nome_comp = st.text_input("Nome do comparativo", placeholder=f"Ex: Comparativo {ano_a} vs {ano_b}", key="comp_nome_input")
        with sv2:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("💾 Salvar", use_container_width=True, key="btn_salvar_comp"):
                if nome_comp:
                    resultado_salvar = {
                        "ano_a": ano_a, "ano_b": ano_b,
                        "label_a": label_a, "label_b": label_b,
                        "tot_a": tot_a, "tot_b": tot_b,
                        "var_total": var_total, "melhor_total": melhor_total,
                        "cat_data": cat_data,
                        "rows_tab": rows_tab,
                        "vm_a": [df_a[df_a["mes"]==m]["valor"].sum() for m in range(1,13)],
                        "vm_b": [df_b[df_b["mes"]==m]["valor"].sum() for m in range(1,13)],
                    }
                    uid_comp = save_comparativo(nome_comp, ano_a, ano_b, resultado_salvar, user["id"])
                    if uid_comp:
                        st.success(f"✅ Comparativo '{nome_comp}' salvo com sucesso!")
                else:
                    st.error("Digite um nome para o comparativo.")

        # ── COMPARATIVOS SALVOS ───────────────────────────────────────────────
        comps_salvos = get_comparativos(usuario_id=user["id"], is_admin=is_admin)
        if comps_salvos:
            st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin:20px 0 8px;">📂 Comparativos Salvos</p>', unsafe_allow_html=True)
            for cp in comps_salvos:
                cc1, cc2, cc3, cc4 = st.columns([3,1.5,1.5,1])
                with cc1:
                    st.markdown(f'<span style="font-weight:600;color:{TEXT};">📊 {cp["nome"]}</span>', unsafe_allow_html=True)
                with cc2:
                    st.markdown(f'<span style="color:{TEXT2};font-size:12px;">{cp["ano_a"]} vs {cp["ano_b"]}</span>', unsafe_allow_html=True)
                with cc3:
                    data_cp = cp.get("criado_em","")[:10] if cp.get("criado_em") else "—"
                    st.markdown(f'<span style="color:{TEXT2};font-size:12px;">🕐 {data_cp}</span>', unsafe_allow_html=True)
                with cc4:
                    if st.button("🗑", key=f"del_comp_{cp['id']}"):
                        delete_comparativo(cp["id"]); st.rerun()
                st.markdown("<hr>", unsafe_allow_html=True)

    else:
        st.markdown(f"""
        <div style="text-align:center;padding:60px 20px;background:{BG3};border-radius:14px;border:1.5px dashed {BORD};margin-top:12px;">
          <div style="font-size:48px;margin-bottom:12px;">📊</div>
          <p style="font-size:15px;font-weight:600;color:{TEXT};">Anexe duas planilhas acima para iniciar o comparativo</p>
          <p style="font-size:13px;color:{TEXT2};margin-top:6px;">Suporta arquivos .xls e .xlsx no formato BASE RAZÃO UNIVISA</p>
        </div>
        """, unsafe_allow_html=True)

    st.stop()

# ── ABA BANCO ────────────────────────────────────────────────────────────────
if st.session_state.aba == "banco":
    st.markdown(f'<h3 style="color:{TEXT};margin:8px 0 16px;">🗄️ Banco de Dados — Planilhas Salvas</h3>', unsafe_allow_html=True)
    uploads = get_uploads(usuario_id=user["id"], is_admin=is_admin)
    if not uploads:
        st.info("Nenhuma planilha salva no banco ainda.")
    else:
        for up in uploads:
            c1,c2,c3,c4,c5 = st.columns([3,1.2,1.5,1.2,1])
            with c1: st.markdown(f'<span style="font-weight:600;color:{TEXT};font-size:13px;">📊 {up["nome_arquivo"]}</span>', unsafe_allow_html=True)
            with c2: st.markdown(f'<span style="color:{TEXT2};font-size:12px;">📅 {up["ano"] or "—"}</span>', unsafe_allow_html=True)
            with c3:
                data = up.get("criado_em","")[:10] if up.get("criado_em") else "—"
                st.markdown(f'<span style="color:{TEXT2};font-size:12px;">🕐 {data}</span>', unsafe_allow_html=True)
            with c4:
                if st.button("📂 Carregar", key=f"bload_{up['id']}"):
                    d, arq, ano_up = load_upload(up["id"])
                    if d:
                        st.session_state.df_records = d
                        st.session_state.arquivo = arq
                        st.session_state.anos_disponiveis = sorted(set(r['ano'] for r in d))
                        st.session_state.aba = "dashboard"; st.rerun()
            with c5:
                if is_admin and st.button("🗑", key=f"bdel_{up['id']}"):
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
    with st.form("form_add_user"):
        col1, col2 = st.columns(2)
        with col1:
            f_login = st.text_input("Login", placeholder="Ex: maria.silva")
            f_nome  = st.text_input("Nome completo", placeholder="Ex: Maria Silva")
        with col2:
            f_senha = st.text_input("Senha", type="password", placeholder="••••••••")
            f_role  = st.selectbox("Perfil", ["user","admin"])
        if st.form_submit_button("✅ Adicionar Usuário", use_container_width=True):
            if f_login and f_senha:
                if add_user(f_login, f_senha, f_nome, f_role):
                    st.success(f"Usuário '{f_login}' adicionado!"); st.rerun()
            else: st.error("Login e senha são obrigatórios.")
    st.markdown(f'<div style="font-size:13px;font-weight:700;color:{TEXT};margin:16px 0 12px;">👥 Usuários Cadastrados</div>', unsafe_allow_html=True)
    for u in get_users():
        cu1,cu2,cu3,cu4 = st.columns([2,2,1.5,1])
        with cu1: st.markdown(f'<span style="font-weight:600;color:{TEXT};">{u["nome"]}</span>', unsafe_allow_html=True)
        with cu2: st.markdown(f'<span style="color:{TEXT2};">@{u["login"]}</span>', unsafe_allow_html=True)
        with cu3: st.markdown(f'<span style="color:{TEXT2};">{"🟠 Admin" if u["role"]=="admin" else "⚪ Usuário"}</span>', unsafe_allow_html=True)
        with cu4:
            if u["role"] != "admin":
                if st.button("🗑 Remover", key=f"rm_{u['id']}"):
                    delete_user(u["id"]); st.rerun()
        st.markdown("<hr>", unsafe_allow_html=True)
    st.stop()

# ── UPLOAD ───────────────────────────────────────────────────────────────────
if st.session_state.df_records:
    col_exp, col_clr = st.columns([9,1])
    with col_clr:
        if st.button("🗑 Limpar", help="Limpar dados"):
            st.session_state.df_records = []
            st.session_state.arquivo = None
            st.session_state.anos_disponiveis = []
            st.rerun()
    with col_exp:
        exp = st.expander("📁 Carregar nova planilha", expanded=False)
else:
    exp = st.expander("📁 Carregar nova planilha (.xls/.xlsx)", expanded=True)

with exp:
    uploaded = st.file_uploader(
        "Arraste ou clique — formato BASE RAZÃO UNIVISA",
        type=["xls","xlsx"], key="file_up"
    )
    if uploaded:
        file_bytes = uploaded.read()
        records, anos = parse_base_razao(file_bytes, uploaded.name)
        if records:
            st.success(f"✓ {len(records)} registros · Anos: {anos} · Arquivo: **{uploaded.name}**")
            cs, ci = st.columns(2)
            with cs:
                if st.button("💾 Salvar no banco", use_container_width=True):
                    uid = save_upload(uploaded.name, str(anos[-1] if anos else "2025"), records, user["id"])
                    if uid:
                        st.session_state.df_records = records
                        st.session_state.arquivo = uploaded.name
                        st.session_state.anos_disponiveis = anos
                        st.rerun()
            with ci:
                if st.button("👁 Visualizar sem salvar", use_container_width=True):
                    st.session_state.df_records = records
                    st.session_state.arquivo = uploaded.name
                    st.session_state.anos_disponiveis = anos
                    st.rerun()
        else:
            st.error("Não foi possível processar. Verifique se a planilha tem aba 'BASE RAZÃO' com colunas DATA, TIPO DE RECEITA, CENTRO DE CUSTO, VALOR.")

# ── DASHBOARD ────────────────────────────────────────────────────────────────
records = st.session_state.df_records

if not records:
    st.markdown(f"""
    <div style="text-align:center;padding:80px 20px;">
      <div style="font-size:52px;margin-bottom:12px;opacity:.3;">📊</div>
      <p style="font-size:16px;font-weight:600;color:{TEXT};">Carregue uma planilha para visualizar os dados</p>
      <p style="font-size:13px;color:{TEXT2};">Use o painel acima ou selecione uma planilha salva na barra lateral</p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

df = pd.DataFrame(records)
anos_disp = sorted(df['ano'].unique().tolist())

# ── FILTROS ───────────────────────────────────────────────────────────────────
st.markdown(f'<div style="font-size:11px;font-weight:700;color:{TEXT2};text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">Filtros</div>', unsafe_allow_html=True)
ff1, ff2, ff3, ff4, ff5 = st.columns([1.5,1.5,1.5,1.5,2])

with ff1:
    anos_opts = ["Todos os Anos"] + [str(a) for a in anos_disp]
    f_ano = st.selectbox("Ano", anos_opts)

with ff2:
    cats = ["Todas","Mensalidades Graduação","Mensalidades Pós-Graduação","Mensalidades CAMB","Mensalidades Taxas","Outras Receitas"]
    f_cat = st.selectbox("Categoria", cats)

with ff3:
    tipos = ["Todos","Mensalidades","Taxas","Outros"]
    f_tipo = st.selectbox("Tipo de Receita", tipos)

with ff4:
    meses_opts = ["Todos os Meses"] + MESES
    f_mes = st.selectbox("Mês", meses_opts)

with ff5:
    f_busca = st.text_input("Buscar curso/centro", placeholder="Nome...")

# Apply filters
fdf = df.copy()
if f_ano != "Todos os Anos":    fdf = fdf[fdf['ano'] == int(f_ano)]
if f_cat != "Todas":            fdf = fdf[fdf['categoria'] == f_cat]
if f_tipo != "Todos":           fdf = fdf[fdf['tipo'] == f_tipo]
if f_mes != "Todos os Meses":   fdf = fdf[fdf['mes_nome'] == f_mes]
if f_busca:                     fdf = fdf[fdf['centro'].str.contains(f_busca, case=False, na=False)]

# ── KPIs ─────────────────────────────────────────────────────────────────────
total_geral = fdf['valor'].sum()
grad_df  = fdf[fdf['categoria']=='Mensalidades Graduação']
camb_df  = fdf[fdf['categoria']=='Mensalidades CAMB']
pos_df   = fdf[fdf['categoria']=='Mensalidades Pós-Graduação']
taxas_df = fdf[fdf['tipo']=='Taxas']

total_grad = grad_df['valor'].sum()
total_camb = camb_df['valor'].sum()
total_pos  = pos_df['valor'].sum()
total_taxa = taxas_df['valor'].sum()

# Maior centro
by_centro = fdf.groupby('centro')['valor'].sum()
maior_centro = by_centro.idxmax() if len(by_centro) else "—"
maior_valor  = by_centro.max() if len(by_centro) else 0

st.markdown("<br>", unsafe_allow_html=True)
k1,k2,k3,k4,k5 = st.columns(5)
kpis_data = [
    (k1, "Total Geral", fmt_short(total_geral), f"{f_ano}" + (f" · {f_mes[:3].capitalize()}" if f_mes != 'Todos os Meses' else ""), True),
    (k2, "Maior Receita", fmt_short(maior_valor), maior_centro[:28], False),
    (k3, "Mensalidades CAMB", fmt_short(total_camb), "colégio aplicação", False),
    (k4, "Mensalidades Taxas", fmt_short(total_taxa), "taxas e emolumentos", False),
    (k5, "Mensalidades Graduação", fmt_short(total_grad), "total graduação", False),
]
# Só mostra Pós se filtro de pós estiver ativo
if f_cat == "Mensalidades Pós-Graduação":
    kpis_data[4] = (k5, "Mensalidades Pós-Grad.", fmt_short(total_pos), "total pós-grad", False)

for col, lbl, val, sub, hl in kpis_data:
    with col:
        st.markdown(f'<div class="kpi-card {"hl" if hl else ""}"><div class="kpi-lbl">{lbl}</div><div class="kpi-val">{val}</div><div class="kpi-sub">{sub}</div></div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── GRÁFICOS ─────────────────────────────────────────────────────────────────
g1, g2 = st.columns(2)

# Top 10 por centro (excluindo pós por padrão, a menos que filtrado)
with g1:
    st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Top 10 Centros por Receita</p>', unsafe_allow_html=True)
    show_df = fdf if f_cat != "Todas" else fdf[fdf['categoria'] != 'PÓS-GRADUAÇÃO']
    top_c = show_df.groupby('centro')['valor'].sum().nlargest(10).reset_index()
    top_c = top_c.sort_values('valor', ascending=True)
    if not top_c.empty:
        clrs = [f"rgba(242,101,34,{0.3+0.7*(i/(len(top_c)-1 if len(top_c)>1 else 1))})" for i in range(len(top_c))]
        fig1 = go.Figure(go.Bar(
            x=top_c['valor'], y=top_c['centro'].str[:35],
            orientation="h", marker_color=clrs,
            customdata=top_c['valor'].apply(fmt_brl),
            hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>"
        ))
        fig1.update_layout(height=300, margin=dict(l=0,r=0,t=0,b=0),
            plot_bgcolor=CHART, paper_bgcolor=CHART, font_family="Sora", showlegend=False,
            yaxis=dict(tickfont=dict(size=10,color=TEXT2)),
            xaxis=dict(tickfont=dict(size=10,color=TEXT2), gridcolor=GRID))
        st.plotly_chart(fig1, use_container_width=True, config={"displayModeBar":False})

# Distribuição por Categoria
with g2:
    st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Distribuição por Categoria</p>', unsafe_allow_html=True)
    by_cat = fdf.groupby('categoria')['valor'].sum().reset_index()
    if not by_cat.empty:
        pal = {"Mensalidades Graduação":"#F26522","Mensalidades CAMB":"#C84E00","Mensalidades Pós-Graduação":"#FF8C42","Outras Receitas":"#FFD5B8","Mensalidades Taxas":"#A03C00","Mensalidades":"#FF8C42"}
        clrs_pie = [pal.get(c,"#FF8C42") for c in by_cat['categoria']]
        fig2 = go.Figure(go.Pie(
            labels=by_cat['categoria'], values=by_cat['valor'],
            hole=.55, marker_colors=clrs_pie,
            customdata=by_cat['valor'].apply(fmt_brl),
            hovertemplate="<b>%{label}</b><br>%{customdata}<br>%{percent}<extra></extra>"
        ))
        fig2.update_layout(height=300, margin=dict(l=0,r=0,t=0,b=0),
            legend=dict(font=dict(size=10,color=TEXT2), orientation="v"),
            paper_bgcolor=CHART, font_family="Sora")
        st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar":False})

# Gráfico Pós-Graduação separado (só quando filtrado)
if f_cat == "Mensalidades Pós-Graduação" and not pos_df.empty:
    st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Detalhamento — Pós-Graduação</p>', unsafe_allow_html=True)
    pos_det = pos_df.groupby('centro')['valor'].sum().reset_index().sort_values('valor', ascending=True)
    clrs_pos = ["#F26522","#FF8C42","#C84E00","#FFB380"]
    fig_pos = go.Figure(go.Bar(
        x=pos_det['valor'], y=pos_det['centro'].str.replace('PÓS GRADUAÇÃO - ','').str.replace('PÓS-GRADUAÇÃO','Geral'),
        orientation="h", marker_color=clrs_pos[:len(pos_det)],
        customdata=pos_det['valor'].apply(fmt_brl),
        hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>"
    ))
    fig_pos.update_layout(height=200, margin=dict(l=0,r=0,t=0,b=0),
        plot_bgcolor=CHART, paper_bgcolor=CHART, font_family="Sora",
        yaxis=dict(tickfont=dict(size=11,color=TEXT2)),
        xaxis=dict(tickfont=dict(size=10,color=TEXT2), gridcolor=GRID))
    st.plotly_chart(fig_pos, use_container_width=True, config={"displayModeBar":False})

# Gráfico CAMB separado
if f_cat == "Mensalidades CAMB" and not camb_df.empty:
    st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Detalhamento — CAMB por Tipo</p>', unsafe_allow_html=True)
    camb_det = camb_df.groupby('tipo')['valor'].sum().reset_index()
    fig_camb = go.Figure(go.Bar(
        x=camb_det['tipo'], y=camb_det['valor'],
        marker_color=["#F26522","#FF8C42","#C84E00"][:len(camb_det)],
        customdata=camb_det['valor'].apply(fmt_brl),
        hovertemplate="<b>%{x}</b><br>%{customdata}<extra></extra>"
    ))
    fig_camb.update_layout(height=220, margin=dict(l=0,r=0,t=0,b=0),
        plot_bgcolor=CHART, paper_bgcolor=CHART, font_family="Sora", showlegend=False,
        xaxis=dict(tickfont=dict(size=11,color=TEXT2)),
        yaxis=dict(tickfont=dict(size=10,color=TEXT2), gridcolor=GRID))
    st.plotly_chart(fig_camb, use_container_width=True, config={"displayModeBar":False})

# Evolução Mensal — comparativo por ano
st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Evolução Mensal{" — Comparativo Anual" if len(anos_disp)>1 and f_ano=="Todos os Anos" else ""}</p>', unsafe_allow_html=True)
pal_anos = ["#F26522","#4A90D9","#27AE60","#9B59B6","#E74C3C"]
fig_ev = go.Figure()
anos_para_plot = anos_disp if (f_ano == "Todos os Anos" and len(anos_disp) > 1) else ([int(f_ano)] if f_ano != "Todos os Anos" else anos_disp)
for i, ano_p in enumerate(anos_para_plot):
    df_ano = fdf[fdf['ano']==ano_p] if f_ano=="Todos os Anos" else fdf
    vals_m = [df_ano[df_ano['mes']==m]['valor'].sum() for m in range(1,13)]
    fig_ev.add_trace(go.Scatter(
        x=MESES_SH, y=vals_m, mode="lines+markers", name=str(ano_p),
        line=dict(color=pal_anos[i%len(pal_anos)], width=2.5),
        fill="tozeroy" if len(anos_para_plot)==1 else None,
        fillcolor="rgba(242,101,34,.08)" if len(anos_para_plot)==1 else None,
        marker=dict(size=6),
        customdata=[fmt_brl(v) for v in vals_m],
        hovertemplate=f"<b>%{{x}} {ano_p}</b><br>%{{customdata}}<extra></extra>"
    ))
fig_ev.update_layout(height=200, margin=dict(l=0,r=0,t=0,b=0),
    plot_bgcolor=CHART, paper_bgcolor=CHART, font_family="Sora",
    legend=dict(font=dict(size=10,color=TEXT2)),
    xaxis=dict(gridcolor=GRID, tickfont=dict(size=11,color=TEXT2)),
    yaxis=dict(gridcolor=GRID, tickfont=dict(size=10,color=TEXT2)))
st.plotly_chart(fig_ev, use_container_width=True, config={"displayModeBar":False})

# ── TABELA ────────────────────────────────────────────────────────────────────
st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin-bottom:4px;">Tabela de Receitas</p>', unsafe_allow_html=True)
pivot = fdf.groupby(['centro','categoria','tipo','mes'])['valor'].sum().reset_index()
pivot_wide = pivot.pivot_table(index=['centro','categoria','tipo'], columns='mes', values='valor', fill_value=0).reset_index()
pivot_wide.columns = [str(c) if isinstance(c,int) else c for c in pivot_wide.columns]
pivot_wide['TOTAL'] = pivot_wide.select_dtypes(include='number').sum(axis=1)
# Rename month columns
for m_num, m_nome in MES_MAP.items():
    if str(m_num) in pivot_wide.columns:
        pivot_wide = pivot_wide.rename(columns={str(m_num): m_nome[:3].capitalize()})
pivot_wide = pivot_wide.rename(columns={'centro':'Centro de Custo','categoria':'Categoria','tipo':'Tipo'})
# Format values
for col in pivot_wide.columns:
    if col not in ['Centro de Custo','Categoria','Tipo']:
        pivot_wide[col] = pivot_wide[col].apply(lambda v: fmt_brl(v) if v and v != 0 else "—")
st.dataframe(pivot_wide, use_container_width=True, height=380)

# ── COMPARATIVO ANO A ANO ─────────────────────────────────────────────────────
if len(anos_disp) > 1 and f_ano == "Todos os Anos":
    st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin:16px 0 8px;">📊 Relatório Comparativo Anual</p>', unsafe_allow_html=True)
    comp_data = []
    for ano_c in anos_disp:
        dfa = df[df['ano']==ano_c]
        comp_data.append({
            'Ano': str(ano_c),
            'Total Geral': fmt_brl(dfa['valor'].sum()),
            'Mensalidades Graduação': fmt_brl(dfa[dfa['categoria']=='Mensalidades Graduação']['valor'].sum()),
            'Mensalidades CAMB': fmt_brl(dfa[dfa['categoria']=='Mensalidades CAMB']['valor'].sum()),
            'Mensalidades Pós-Grad.': fmt_brl(dfa[dfa['categoria']=='Mensalidades Pós-Graduação']['valor'].sum()),
            'Taxas': fmt_brl(dfa[dfa['tipo']=='Taxas']['valor'].sum()),
        })
    st.dataframe(pd.DataFrame(comp_data), use_container_width=True, hide_index=True)

# ── EXPORTAR ─────────────────────────────────────────────────────────────────
st.markdown(f'<p style="font-size:13px;font-weight:700;color:{TEXT};margin:16px 0 8px;">📤 Exportar Relatório</p>', unsafe_allow_html=True)
ex1, ex2, _ = st.columns([1.2, 1.2, 5])

with ex1:
    if st.button("🌐 HTML Interativo", use_container_width=True):
        try:
            import plotly.io as _pio

            top_c2 = fdf.groupby('centro')['valor'].sum().nlargest(10).reset_index().sort_values('valor', ascending=True)
            top_c2['ValorFmt'] = top_c2['valor'].apply(fmt_brl)
            clrs2 = [f"rgba(242,101,34,{0.3+0.7*(i/(len(top_c2)-1 if len(top_c2)>1 else 1))})" for i in range(len(top_c2))]
            fb = go.Figure(go.Bar(x=top_c2['valor'], y=top_c2['centro'].str[:35], orientation="h",
                marker_color=clrs2, customdata=top_c2['ValorFmt'],
                hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>"))
            fb.update_layout(title="Top 10 por Receita", height=400, margin=dict(l=160,r=20,t=40,b=0),
                plot_bgcolor="white", paper_bgcolor="white", font_family="Sora",
                yaxis=dict(tickfont=dict(size=10)), xaxis=dict(tickformat=",.0f", tickprefix="R$"))

            by_cat2 = fdf.groupby('categoria')['valor'].sum().reset_index()
            pal2 = {"Mensalidades Graduação":"#F26522","Mensalidades CAMB":"#C84E00","Mensalidades Pós-Graduação":"#FF8C42","Outras Receitas":"#FFD5B8","Mensalidades Taxas":"#A03C00"}
            fp = go.Figure(go.Pie(labels=by_cat2['categoria'], values=by_cat2['valor'], hole=.5,
                marker_colors=[pal2.get(c,"#FF8C42") for c in by_cat2['categoria']],
                hovertemplate="<b>%{label}</b><br>%{percent}<extra></extra>"))
            fp.update_layout(title="Distribuição por Categoria", height=400, margin=dict(l=0,r=0,t=40,b=0),
                paper_bgcolor="white", font_family="Sora")

            vals_ev = []
            for ano_p2 in anos_para_plot:
                dfa2 = fdf[fdf['ano']==ano_p2] if f_ano=="Todos os Anos" else fdf
                vals_ev.append([dfa2[dfa2['mes']==m]['valor'].sum() for m in range(1,13)])

            fl_fig = go.Figure()
            for i2, (ano_p2, vm) in enumerate(zip(anos_para_plot, vals_ev)):
                fl_fig.add_trace(go.Scatter(x=MESES_SH, y=vm, mode="lines+markers", name=str(ano_p2),
                    line=dict(color=pal_anos[i2%len(pal_anos)], width=2.5), marker=dict(size=6)))
            fl_fig.update_layout(title="Evolução Mensal", height=300, margin=dict(l=0,r=0,t=40,b=0),
                plot_bgcolor="white", paper_bgcolor="white", font_family="Sora")

            bar_d  = _json.loads(_pio.to_json(fb))
            pie_d  = _json.loads(_pio.to_json(fp))
            line_d = _json.loads(_pio.to_json(fl_fig))

            th = "".join(f"<th>{c}</th>" for c in pivot_wide.columns)
            tr = "".join("<tr>"+"".join(f"<td>{v}</td>" for v in row)+"</tr>" for _,row in pivot_wide.iterrows())

            html_out = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<title>UNIVISA Receitas</title><script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>*{{box-sizing:border-box;margin:0;padding:0;}}body{{font-family:'Sora',sans-serif;background:#FFF8F4;}}
.hdr{{background:linear-gradient(90deg,#1a0a00,#3d1500);padding:14px 28px;display:flex;align-items:center;gap:14px;border-bottom:3px solid #F26522;}}
.hdr h1{{color:white;font-size:20px;}}.badge{{background:rgba(242,101,34,.25);color:#FF8C42;font-size:11px;font-weight:700;padding:3px 12px;border-radius:20px;border:1px solid rgba(242,101,34,.4);}}
.wrap{{max-width:1400px;margin:0 auto;padding:24px 20px;}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px;}}
.kpi{{background:white;border:1.5px solid #FFD5B8;border-radius:12px;padding:14px 16px;position:relative;overflow:hidden;}}
.kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:#F26522;}}
.kpi.hl{{background:#F26522;border-color:#F26522;}}.kpi-lbl{{font-size:9px;font-weight:700;color:#C84E00;text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px;}}
.kpi.hl .kpi-lbl{{color:rgba(255,255,255,.8);}}.kpi-val{{font-size:18px;font-weight:700;color:#111;}}.kpi.hl .kpi-val{{color:white;}}
.kpi-sub{{font-size:10px;color:#C84E00;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}.kpi.hl .kpi-sub{{color:rgba(255,255,255,.7);}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;}}.box{{background:white;border:1.5px solid #FFD5B8;border-radius:12px;padding:14px;}}
.stitle{{font-size:12px;font-weight:700;color:#111;margin-bottom:8px;}}
table{{width:100%;border-collapse:collapse;background:white;border-radius:12px;overflow:hidden;border:1.5px solid #FFD5B8;font-size:11px;}}
th{{padding:8px 10px;background:#F26522;color:white;font-size:10px;text-align:left;font-weight:700;}}
td{{padding:5px 10px;border-bottom:1px solid #FFE5D0;}}tr:hover td{{background:#FFF3EC;}}
.footer{{font-size:10px;color:#C84E00;margin-top:14px;display:flex;gap:20px;flex-wrap:wrap;}}
</style></head><body>
<div class="hdr">
  <div style="background:white;border-radius:7px;padding:4px 8px;font-size:13px;font-weight:800;color:#F26522;">UV</div>
  <h1>UNIVISA <span style="color:#FF8C42;">Receitas</span></h1>
  <span class="badge">{f_ano}</span>{"<span class='badge'>"+f_mes+"</span>" if f_mes!="Todos os Meses" else ""}{"<span class='badge'>"+f_cat+"</span>" if f_cat!="Todas" else ""}
</div>
<div class="wrap">
  <div class="kpis">
    <div class="kpi hl"><div class="kpi-lbl">Total Geral</div><div class="kpi-val">{fmt_short(total_geral)}</div><div class="kpi-sub">{f_ano}</div></div>
    <div class="kpi"><div class="kpi-lbl">Maior Receita</div><div class="kpi-val">{fmt_short(maior_valor)}</div><div class="kpi-sub">{maior_centro[:28]}</div></div>
    <div class="kpi"><div class="kpi-lbl">CAMB</div><div class="kpi-val">{fmt_short(total_camb)}</div><div class="kpi-sub">colégio aplicação</div></div>
    <div class="kpi"><div class="kpi-lbl">Graduação</div><div class="kpi-val">{fmt_short(total_grad)}</div><div class="kpi-sub">mensalidades</div></div>
    <div class="kpi"><div class="kpi-lbl">Taxas</div><div class="kpi-val">{fmt_short(total_taxa)}</div><div class="kpi-sub">taxas e emolumentos</div></div>
  </div>
  <div class="charts">
    <div class="box"><div class="stitle">Top 10 por Receita</div><div id="cbar"></div></div>
    <div class="box"><div class="stitle">Distribuição por Categoria</div><div id="cpie"></div></div>
  </div>
  <div class="box" style="margin-bottom:16px;"><div class="stitle">Evolução Mensal</div><div id="cline"></div></div>
  <div class="stitle" style="margin-bottom:8px;">Tabela de Receitas</div>
  <table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>
  <div class="footer"><span><b>Arquivo:</b> {st.session_state.arquivo or "—"}</span><span><b>Gerado:</b> {datetime.now().strftime("%d/%m/%Y %H:%M")}</span><span><b>Usuário:</b> {nome}</span></div>
</div>
<script>
var bd={_json.dumps(bar_d)};var pd2={_json.dumps(pie_d)};var ld={_json.dumps(line_d)};
Plotly.newPlot('cbar',bd.data,bd.layout,{{responsive:true,displayModeBar:true}});
Plotly.newPlot('cpie',pd2.data,pd2.layout,{{responsive:true,displayModeBar:true}});
Plotly.newPlot('cline',ld.data,ld.layout,{{responsive:true,displayModeBar:true}});
</script></body></html>"""
            st.download_button("⬇️ Baixar HTML", data=html_out.encode("utf-8"),
                file_name=f"UNIVISA_Receitas_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                mime="text/html", use_container_width=True)
        except Exception as e:
            st.error(f"Erro ao gerar HTML: {e}")

with ex2:
    if st.button("📄 PDF", use_container_width=True):
        try:
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib import colors as rl_colors
            from reportlab.lib.units import cm
            import plotly.io as _pio

            OR = rl_colors.HexColor("#F26522")
            DK = rl_colors.HexColor("#1a0a00")
            LT = rl_colors.HexColor("#FFD5B8")

            top_c3 = fdf.groupby('centro')['valor'].sum().nlargest(10).reset_index().sort_values('valor', ascending=True)
            top_c3['ValorFmt'] = top_c3['valor'].apply(fmt_brl)
            clrs3 = [f"rgba(242,101,34,{0.3+0.7*(i/(len(top_c3)-1 if len(top_c3)>1 else 1))})" for i in range(len(top_c3))]
            fb2 = go.Figure(go.Bar(x=top_c3['valor'], y=top_c3['centro'].str[:35], orientation="h",
                marker_color=clrs3, customdata=top_c3['ValorFmt'],
                hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>"))
            fb2.update_layout(title="Top 10 por Receita", height=380, margin=dict(l=160,r=20,t=40,b=0),
                plot_bgcolor="white", paper_bgcolor="white", font_family="Sora",
                yaxis=dict(tickfont=dict(size=10)), xaxis=dict(tickformat=",.0f"))

            by_cat3 = fdf.groupby('categoria')['valor'].sum().reset_index()
            pal3 = {"Mensalidades Graduação":"#F26522","Mensalidades CAMB":"#C84E00","Mensalidades Pós-Graduação":"#FF8C42","Outras Receitas":"#FFD5B8","Mensalidades Taxas":"#A03C00"}
            fp2 = go.Figure(go.Pie(labels=by_cat3['categoria'], values=by_cat3['valor'], hole=.5,
                marker_colors=[pal3.get(c,"#FF8C42") for c in by_cat3['categoria']]))
            fp2.update_layout(title="Distribuição", height=380, margin=dict(l=0,r=0,t=40,b=0),
                paper_bgcolor="white", font_family="Sora")

            def fig2img(f, w=700, h=380):
                return _io.BytesIO(_pio.to_image(f, format="png", width=w, height=h, scale=2))

            ib2 = fig2img(fb2); ip2 = fig2img(fp2)
            buf = _io.BytesIO()
            doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                leftMargin=1.5*cm, rightMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
            ss = getSampleStyleSheet()
            S = lambda n, **kw: ParagraphStyle(n, parent=ss["Normal"], **kw)
            story = [
                Paragraph(f"UNIVISA Receitas — {f_ano}", S("T",fontSize=16,fontName="Helvetica-Bold",textColor=DK,spaceAfter=4)),
                Paragraph(f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} · {nome}" + (f" · {f_cat}" if f_cat!="Todas" else ""), S("S",fontSize=9,textColor=rl_colors.HexColor("#C84E00"),spaceAfter=10)),
            ]
            pw = landscape(A4)[0] - 3*cm
            kd = [["TOTAL GERAL","CAMB","GRADUAÇÃO","PÓS-GRAD.","TAXAS"],
                  [fmt_short(total_geral), fmt_short(total_camb), fmt_short(total_grad), fmt_short(total_pos), fmt_short(total_taxa)]]
            kt = Table(kd, colWidths=[pw/5]*5)
            kt.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(0,1),OR),("TEXTCOLOR",(0,0),(0,1),rl_colors.white),
                ("BACKGROUND",(1,0),(-1,0),rl_colors.HexColor("#FFF3EC")),("TEXTCOLOR",(1,0),(-1,0),rl_colors.HexColor("#C84E00")),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,0),8),
                ("FONTNAME",(0,1),(-1,1),"Helvetica-Bold"),("FONTSIZE",(0,1),(-1,1),13),
                ("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                ("BOX",(0,0),(-1,-1),1,LT),("GRID",(0,0),(-1,-1),.5,LT),
                ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
            ]))
            story += [kt, Spacer(1,10)]
            hw = (pw-.5*cm)/2; ch = 6*cm
            ib2.seek(0); ip2.seek(0)
            ct = Table([[RLImage(ib2,width=hw,height=ch), RLImage(ip2,width=hw,height=ch)]], colWidths=[hw,hw])
            ct.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("RIGHTPADDING",(0,0),(0,-1),6)]))
            story.append(ct)
            story.append(Spacer(1,8))
            max_r = 40
            td = [list(pivot_wide.columns)] + [list(r) for _,r in pivot_wide.head(max_r).iterrows()]
            cw = pw/len(pivot_wide.columns)
            tbl = Table(td, colWidths=[cw]*len(pivot_wide.columns), repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),OR),("TEXTCOLOR",(0,0),(-1,0),rl_colors.white),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[rl_colors.white,rl_colors.HexColor("#FFF8F4")]),
                ("GRID",(0,0),(-1,-1),.3,LT),
                ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
                ("ALIGN",(1,0),(-1,-1),"RIGHT"),
            ]))
            story.append(tbl)
            doc.build(story)
            buf.seek(0)
            st.download_button("⬇️ Baixar PDF", data=buf,
                file_name=f"UNIVISA_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                mime="application/pdf", use_container_width=True)
        except Exception as e:
            st.error(f"Erro ao gerar PDF: {e}")

st.markdown(f"""
<div style="padding:8px 0;font-size:11px;color:{TEXT2};display:flex;gap:22px;flex-wrap:wrap;margin-top:6px;">
  <span><strong style="color:{TEXT}">Arquivo:</strong> {st.session_state.arquivo or 'Nenhum'}</span>
  <span><strong style="color:{TEXT}">Registros:</strong> {len(records)}</span>
  <span><strong style="color:{TEXT}">Usuário:</strong> {nome}</span>
  <span><strong style="color:{TEXT}">Atualizado:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</span>
</div>
""", unsafe_allow_html=True)
