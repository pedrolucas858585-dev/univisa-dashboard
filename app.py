import streamlit as st
import pandas as pd
import json
import hashlib
from supabase import create_client, Client
from datetime import datetime
import plotly.express as px
import plotly.graph_objects as go

# ─── CONFIG ───────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="UNIVISA — Dashboard de Receitas",
    page_icon="🟠",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ─── SUPABASE ─────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    url  = st.secrets["SUPABASE_URL"]
    key  = st.secrets["SUPABASE_KEY"]
    from supabase import ClientOptions
    return create_client(url, key, options=ClientOptions(
        headers={"apikey": key, "Authorization": f"Bearer {key}"}
    ))

supabase = get_supabase()

# ─── HELPERS ──────────────────────────────────────────────────────────────────
MESES    = ['JANEIRO','FEVEREIRO','MARÇO','ABRIL','MAIO','JUNHO',
            'JULHO','AGOSTO','SETEMBRO','OUTUBRO','NOVEMBRO','DEZEMBRO']
MESES_SH = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']

def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()

def fmt_brl(v):
    if not v or v == 0:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_short(v):
    if not v or v == 0:
        return "—"
    if v >= 1_000_000:
        return f"R${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"R${v/1_000:.0f}K"
    return fmt_brl(v)

# ─── AUTH ─────────────────────────────────────────────────────────────────────
def login(login_str: str, senha: str):
    try:
        res = supabase.table("users")\
            .select("*")\
            .eq("login", login_str.lower().strip())\
            .eq("senha_hash", hash_senha(senha))\
            .execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        st.error(f"Erro ao conectar ao banco: {e}")
    return None

def get_users():
    try:
        res = supabase.table("users").select("id,login,nome,role").execute()
        return res.data or []
    except:
        return []

def add_user(login_str, senha, nome, role="user"):
    try:
        supabase.table("users").insert({
            "login": login_str.lower().strip(),
            "senha_hash": hash_senha(senha),
            "nome": nome or login_str,
            "role": role
        }).execute()
        return True
    except Exception as e:
        st.error(str(e))
        return False

def delete_user(user_id):
    try:
        supabase.table("users").delete().eq("id", user_id).execute()
        return True
    except:
        return False

# ─── PLANILHAS ────────────────────────────────────────────────────────────────
def save_upload(nome_arquivo: str, ano: str, dados: list, usuario_id: int):
    try:
        res = supabase.table("uploads").insert({
            "nome_arquivo": nome_arquivo,
            "ano": ano,
            "dados": json.dumps(dados),
            "usuario_id": usuario_id,
            "criado_em": datetime.now().isoformat()
        }).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        st.error(f"Erro ao salvar: {e}")
        return None

def get_uploads():
    try:
        res = supabase.table("uploads")\
            .select("id,nome_arquivo,ano,criado_em,usuario_id")\
            .order("criado_em", desc=True)\
            .execute()
        return res.data or []
    except:
        return []

def load_upload(upload_id: int):
    try:
        res = supabase.table("uploads").select("dados,nome_arquivo,ano").eq("id", upload_id).execute()
        if res.data:
            row = res.data[0]
            return json.loads(row["dados"]), row["nome_arquivo"], row["ano"]
    except Exception as e:
        st.error(f"Erro ao carregar: {e}")
    return None, None, None

def delete_upload(upload_id: int):
    try:
        supabase.table("uploads").delete().eq("id", upload_id).execute()
        return True
    except:
        return False

# ─── PARSER XLSX ──────────────────────────────────────────────────────────────
def parse_value(v):
    if v is None or v == "" or v == 0:
        return 0.0
    if isinstance(v, (int, float)):
        return abs(float(v))
    s = str(v).strip()
    import re
    s = re.sub(r"[a-zA-Z\s]", "", s)
    if not s:
        return 0.0
    if re.match(r"^\d{1,3}(\.\d{3})+,\d+$", s):
        return abs(float(s.replace(".", "").replace(",", ".")))
    return abs(float(s.replace(",", ".")) or 0)

def parse_sheet(raw):
    L0 = {"RECEITA","TAXAS","GRADUAÇÃO","PÓS-GRADUAÇÃO","CAMB"}
    AREA_PARTS = ["CIÊNCIAS EXATAS","CIÊNCIAS HUMANAS","CIÊNCIAS LINGUÍSTICAS",
                  "CIÊNCIAS SOCIAIS","CIÊNCIAS DA SAÚDE","CIÊNCIA DA SAÚDE","CIÊNCIAS TECNOLOGIAS"]
    
    hRow = -1
    for i, row in enumerate(raw):
        if any(str(c or "").strip().upper() == "JANEIRO" for c in row):
            hRow = i
            break
    if hRow < 0:
        return [], None

    hdrs = [str(c or "").strip().upper() for c in raw[hRow]]
    mIdx = {m: hdrs.index(m) for m in MESES if m in hdrs}
    totCol = hdrs.index("TOTAL") if "TOTAL" in hdrs else -1

    # Detect year
    ano = None
    try:
        import re
        title = str((raw[1] or [None]*2)[1] or "")
        m = re.search(r"20\d\d", title)
        if m:
            ano = m.group(0)
    except:
        pass

    rows = []
    cat, area = "", ""

    for i in range(hRow+1, len(raw)):
        row = raw[i]
        if not row:
            continue
        raw_name = row[1] if len(row) > 1 else None
        if raw_name is None:
            continue
        name = str(raw_name).strip()
        if not name:
            continue
        nu = name.upper().strip()

        nivel = 2
        if nu in L0 or nu.split(" ")[0] in L0:
            nivel, cat, area = 0, name, ""
        elif any(p[:14] in nu for p in AREA_PARTS):
            nivel, area = 1, name
        
        meses = {}
        for m in MESES:
            idx = mIdx.get(m)
            meses[m] = parse_value(row[idx]) if idx is not None and idx < len(row) else 0.0

        total = 0.0
        if totCol >= 0 and totCol < len(row) and row[totCol] is not None:
            total = parse_value(row[totCol])
        if not total and len(row) > 17 and row[17] is not None:
            total = parse_value(row[17])
        if not total:
            total = sum(meses.values())

        rows.append({
            "nome": name,
            "nivel": nivel,
            "categoria": cat,
            "area": area,
            "meses": meses,
            "total": total
        })

    return rows, ano

# ─── CUSTOM CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --orange: #F26522;
  --orange-dark: #C84E00;
  --orange-pale: #FFF3EC;
}
html, body, [class*="css"] {
  font-family: 'Sora', sans-serif !important;
}
.stApp {
  background: #FAFAFA;
}
/* KPI cards */
.kpi-card {
  background: white;
  border: 1.5px solid #EFEFEF;
  border-radius: 14px;
  padding: 18px 20px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0,0,0,.06);
}
.kpi-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: #F26522;
}
.kpi-card.highlight {
  background: #F26522;
  border-color: #F26522;
}
.kpi-card.highlight::before {
  background: rgba(255,255,255,.3);
}
.kpi-label {
  font-size: 10px;
  font-weight: 700;
  color: #666;
  text-transform: uppercase;
  letter-spacing: .5px;
  margin-bottom: 6px;
}
.kpi-card.highlight .kpi-label { color: rgba(255,255,255,.75); }
.kpi-value {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -.5px;
  color: #111;
}
.kpi-card.highlight .kpi-value { color: white; }
.kpi-sub {
  font-size: 11px;
  color: #AAA;
  margin-top: 3px;
}
.kpi-card.highlight .kpi-sub { color: rgba(255,255,255,.65); }
/* Header */
.dash-header {
  background: white;
  border-bottom: 2.5px solid #F26522;
  padding: 14px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  box-shadow: 0 2px 12px rgba(242,101,34,.08);
}
/* Streamlit tweaks */
.block-container { padding-top: 1rem !important; }
div[data-testid="stMetric"] label { font-family: 'Sora', sans-serif !important; }
</style>
""", unsafe_allow_html=True)

# ─── SESSION ──────────────────────────────────────────────────────────────────
if "user" not in st.session_state:
    st.session_state.user = None
if "dados" not in st.session_state:
    st.session_state.dados = []
if "ano" not in st.session_state:
    st.session_state.ano = "2025"
if "arquivo" not in st.session_state:
    st.session_state.arquivo = None

# ─── LOGIN ────────────────────────────────────────────────────────────────────
if st.session_state.user is None:
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("""
        <div style="background:linear-gradient(135deg,#1a0a00,#3d1500,#F26522);
                    border-radius:20px;padding:40px;margin-top:60px;
                    box-shadow:0 16px 48px rgba(0,0,0,.3);">
          <div style="background:white;border-radius:14px;padding:36px 32px;">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px;">
              <div style="width:44px;height:44px;background:#F26522;border-radius:11px;
                          display:flex;align-items:center;justify-content:center;
                          font-size:18px;font-weight:800;color:white;">UV</div>
              <div style="font-size:20px;font-weight:700;">UNIVISA <span style="color:#F26522">Receitas</span></div>
            </div>
            <p style="font-size:13px;color:#666;margin-bottom:8px;">
              Acesse com seu login e senha para visualizar o dashboard financeiro.
            </p>
          </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        with st.form("login_form"):
            login_input = st.text_input("Login", placeholder="Ex: joao.silva")
            senha_input = st.text_input("Senha", type="password", placeholder="••••••••")
            submitted = st.form_submit_button("→ Entrar", use_container_width=True)
            
            if submitted:
                if login_input and senha_input:
                    u = login(login_input, senha_input)
                    if u:
                        st.session_state.user = u
                        st.rerun()
                    else:
                        st.error("Usuário ou senha incorretos.")
                else:
                    st.warning("Preencha login e senha.")
        
        st.markdown("""
        <p style="text-align:center;font-size:11px;color:#AAA;margin-top:12px;">
          ASSOCIAÇÃO DO ENSINO SUPERIOR DA VITÓRIA STO ANTÃO
        </p>
        """, unsafe_allow_html=True)
    st.stop()

# ─── APP (AUTENTICADO) ────────────────────────────────────────────────────────
user = st.session_state.user
is_admin = user.get("role") == "admin"

# TOPBAR
col_brand, col_right = st.columns([3, 1])
with col_brand:
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:10px;padding:10px 0;">
      <div style="width:34px;height:34px;background:#F26522;border-radius:9px;
                  display:flex;align-items:center;justify-content:center;
                  font-size:14px;font-weight:800;color:white;">UV</div>
      <span style="font-size:17px;font-weight:700;">
        UNIVISA <span style="color:#F26522">Receitas</span>
      </span>
      <span style="background:#FFF3EC;color:#C84E00;font-size:11px;font-weight:700;
                   padding:3px 11px;border-radius:20px;border:1px solid #FFD5B8;">
        {st.session_state.ano}
      </span>
    </div>
    """, unsafe_allow_html=True)
with col_right:
    st.markdown(f"""
    <div style="display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:10px 0;">
      <div style="background:#F26522;width:26px;height:26px;border-radius:50%;
                  display:flex;align-items:center;justify-content:center;
                  font-size:11px;font-weight:700;color:white;">
        {(user.get('nome') or user['login'])[:2].upper()}
      </div>
      <span style="font-size:12px;font-weight:600;">{user.get('nome') or user['login']}</span>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# SIDEBAR
with st.sidebar:
    st.markdown("### ⚙️ Painel")
    if st.button("🚪 Sair", use_container_width=True):
        st.session_state.user = None
        st.session_state.dados = []
        st.rerun()
    
    st.divider()
    
    # Histórico de uploads
    st.markdown("#### 📂 Planilhas Salvas")
    uploads = get_uploads()
    if uploads:
        for up in uploads:
            col_up, col_del = st.columns([3, 1])
            with col_up:
                if st.button(f"📊 {up['ano']} — {up['nome_arquivo'][:20]}", key=f"load_{up['id']}", use_container_width=True):
                    dados, arq, ano = load_upload(up["id"])
                    if dados:
                        st.session_state.dados = dados
                        st.session_state.arquivo = arq
                        st.session_state.ano = ano or "2025"
                        st.rerun()
            if is_admin:
                with col_del:
                    if st.button("🗑", key=f"del_{up['id']}"):
                        delete_upload(up["id"])
                        st.rerun()
    else:
        st.caption("Nenhuma planilha salva ainda.")
    
    # Gerenciar usuários (admin)
    if is_admin:
        st.divider()
        st.markdown("#### 👤 Usuários")
        with st.expander("Adicionar usuário"):
            with st.form("add_user_form"):
                nu_login = st.text_input("Login")
                nu_senha = st.text_input("Senha", type="password")
                nu_nome  = st.text_input("Nome completo")
                nu_role  = st.selectbox("Perfil", ["user","admin"])
                if st.form_submit_button("Adicionar"):
                    if nu_login and nu_senha:
                        if add_user(nu_login, nu_senha, nu_nome, nu_role):
                            st.success(f"Usuário '{nu_login}' adicionado!")
                    else:
                        st.error("Login e senha obrigatórios.")
        
        users_list = get_users()
        for u in users_list:
            col_u, col_d = st.columns([3,1])
            with col_u:
                st.markdown(f"**{u['nome']}** `{u['role']}`")
            with col_d:
                if u["role"] != "admin" and st.button("✕", key=f"du_{u['id']}"):
                    delete_user(u["id"])
                    st.rerun()

# ─── UPLOAD ───────────────────────────────────────────────────────────────────
with st.expander("📁 Carregar nova planilha .xlsx", expanded=not bool(st.session_state.dados)):
    uploaded = st.file_uploader(
        "Arraste ou clique — formato Relatório de Receitas Líquidas UNIVISA",
        type=["xlsx","xls"],
        label_visibility="visible"
    )
    if uploaded:
        import openpyxl
        wb = openpyxl.load_workbook(uploaded, data_only=True)
        ws = wb.active
        raw = [[cell.value for cell in row] for row in ws.iter_rows()]
        dados, ano = parse_sheet(raw)
        if dados:
            st.success(f"✓ {len(dados)} registros carregados de **{uploaded.name}**")
            col_sv, col_ig = st.columns(2)
            with col_sv:
                if st.button("💾 Salvar no banco de dados", use_container_width=True):
                    uid = save_upload(uploaded.name, ano or "2025", dados, user["id"])
                    if uid:
                        st.session_state.dados = dados
                        st.session_state.arquivo = uploaded.name
                        st.session_state.ano = ano or "2025"
                        st.success("Salvo com sucesso!")
                        st.rerun()
            with col_ig:
                if st.button("👁 Visualizar sem salvar", use_container_width=True):
                    st.session_state.dados = dados
                    st.session_state.arquivo = uploaded.name
                    st.session_state.ano = ano or "2025"
                    st.rerun()
        else:
            st.error("Não foi possível interpretar a planilha. Verifique se é um Relatório de Receitas Líquidas UNIVISA.")

# ─── DASHBOARD ────────────────────────────────────────────────────────────────
dados = st.session_state.dados

if not dados:
    st.markdown("""
    <div style="text-align:center;padding:80px 20px;color:#AAA;">
      <div style="font-size:52px;margin-bottom:12px;opacity:.4;">📊</div>
      <p style="font-size:16px;font-weight:600;">Carregue uma planilha para visualizar os dados</p>
      <p style="font-size:13px;">Use o painel acima ou selecione uma planilha salva na barra lateral</p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# FILTROS
st.markdown("#### 🔍 Filtros")
col_f1, col_f2, col_f3, col_f4 = st.columns([2,2,2,3])

cats  = sorted(set(r["nome"] for r in dados if r["nivel"] == 0))
areas = sorted(set(r["nome"] for r in dados if r["nivel"] == 1))

with col_f1:
    f_cat = st.selectbox("Categoria", ["Todas"] + cats, key="f_cat")
with col_f2:
    f_area = st.selectbox("Área", ["Todas"] + areas, key="f_area")
with col_f3:
    f_mes = st.selectbox("Mês", ["Todos os Meses"] + MESES, key="f_mes")
with col_f4:
    f_busca = st.text_input("Buscar curso", placeholder="Nome do curso...", key="f_busca")

# Aplicar filtros
filtered = dados.copy()
if f_cat != "Todas":
    filtered = [r for r in filtered if r["categoria"] == f_cat]
if f_area != "Todas":
    filtered = [r for r in filtered if r["area"] == f_area or r["nome"] == f_area]
if f_busca:
    filtered = [r for r in filtered if f_busca.lower() in r["nome"].lower()]

mes_sel = None if f_mes == "Todos os Meses" else f_mes

def get_v(r, mes):
    return r["meses"].get(mes, 0) if mes else r["total"]

# KPIs
cursos   = [r for r in filtered if r["nivel"] == 2]
vals     = [get_v(r, mes_sel) for r in cursos]
total    = sum(vals)
maior    = max(cursos, key=lambda r: get_v(r, mes_sel), default=None)
pos_c    = [r for r in cursos if "PÓS" in r.get("categoria","").upper()]
pos_t    = sum(get_v(r, mes_sel) for r in pos_c)
media    = total / len(cursos) if cursos else 0
qtd_com_receita = sum(1 for v in vals if v > 0)

st.markdown("---")
c1, c2, c3, c4, c5 = st.columns(5)
kpis = [
    (c1, "Total Geral", fmt_short(total), f"{st.session_state.ano}" + (f" · {mes_sel[:3].capitalize()}" if mes_sel else ""), True),
    (c2, "Maior Receita", fmt_short(get_v(maior, mes_sel)) if maior else "—",
     (maior["nome"][:28] if maior else "—"), False),
    (c3, "Qtd. Cursos", str(qtd_com_receita), "com receita", False),
    (c4, "Pós-Graduação", fmt_short(pos_t), "total pós", False),
    (c5, "Média por Curso", fmt_short(media), "receita média", False),
]
for col, label, val, sub, hl in kpis:
    hl_class = "highlight" if hl else ""
    with col:
        st.markdown(f"""
        <div class="kpi-card {hl_class}">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value">{val}</div>
          <div class="kpi-sub">{sub}</div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# GRÁFICOS
col_g1, col_g2 = st.columns(2)

with col_g1:
    st.markdown("**Top 10 Cursos por Receita**")
    top10 = sorted(
        [(r["nome"].replace("Bacharelado em ","").replace("Licenciatura em ","")
          .replace("Tecnólogo em ","")[:35], get_v(r, mes_sel))
         for r in cursos if get_v(r, mes_sel) > 0],
        key=lambda x: x[1], reverse=True
    )[:10]
    if top10:
        df_top = pd.DataFrame(top10, columns=["Curso","Receita"])
        colors = [f"rgba(242,101,34,{1-i*0.08})" for i in range(len(df_top))]
        fig = px.bar(df_top, x="Receita", y="Curso", orientation="h",
                     color_discrete_sequence=colors)
        fig.update_layout(
            showlegend=False, height=300, margin=dict(l=0,r=0,t=0,b=0),
            plot_bgcolor="white", paper_bgcolor="white",
            font_family="Sora",
            yaxis=dict(autorange="reversed", tickfont=dict(size=10)),
            xaxis=dict(tickfont=dict(size=10), gridcolor="#F0F0F0")
        )
        fig.update_traces(marker_color=colors)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("Sem dados para exibir.")

with col_g2:
    st.markdown("**Distribuição por Área**")
    by_area = {}
    for r in cursos:
        if r.get("area") and get_v(r, mes_sel) > 0:
            by_area[r["area"]] = by_area.get(r["area"], 0) + get_v(r, mes_sel)
    if by_area:
        pal = ["#F26522","#FF8C42","#C84E00","#FFB380","#E05A00","#FFD5B8","#A03C00","#FFC4A0"]
        fig2 = go.Figure(go.Pie(
            labels=list(by_area.keys()),
            values=list(by_area.values()),
            hole=.6,
            marker_colors=pal[:len(by_area)],
            textfont_size=10,
        ))
        fig2.update_layout(
            height=300, margin=dict(l=0,r=0,t=0,b=0),
            legend=dict(font=dict(size=10), orientation="v"),
            paper_bgcolor="white", font_family="Sora"
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.caption("Sem dados para exibir.")

# EVOLUÇÃO MENSAL
st.markdown("**Evolução Mensal das Receitas**")
vals_mes = [sum(r["meses"].get(m, 0) for r in cursos) for m in MESES]
fig3 = go.Figure(go.Scatter(
    x=MESES_SH, y=vals_mes,
    mode="lines+markers",
    line=dict(color="#F26522", width=2.5),
    fill="tozeroy",
    fillcolor="rgba(242,101,34,.08)",
    marker=dict(color="#F26522", size=6)
))
fig3.update_layout(
    height=180, margin=dict(l=0,r=0,t=0,b=0),
    plot_bgcolor="white", paper_bgcolor="white",
    font_family="Sora",
    xaxis=dict(gridcolor="#F0F0F0", tickfont=dict(size=11)),
    yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(size=10)),
    showlegend=False
)
st.plotly_chart(fig3, use_container_width=True)

# TABELA
st.markdown("**Tabela de Receitas**")
if mes_sel:
    df_table = pd.DataFrame([
        {"Centro de Custo / Curso": r["nome"],
         "Nível": r["nivel"],
         mes_sel.capitalize(): r["meses"].get(mes_sel, 0)}
        for r in filtered
    ])
    df_show = df_table[["Centro de Custo / Curso", mes_sel.capitalize()]].copy()
    df_show[mes_sel.capitalize()] = df_show[mes_sel.capitalize()].apply(
        lambda v: fmt_brl(v) if v else "—"
    )
else:
    records = []
    for r in filtered:
        row = {"Centro de Custo / Curso": r["nome"]}
        for m, ms in zip(MESES, MESES_SH):
            row[ms] = r["meses"].get(m, 0)
        row["Total"] = r["total"]
        records.append(row)
    df_show = pd.DataFrame(records)
    for col in MESES_SH + ["Total"]:
        if col in df_show.columns:
            df_show[col] = df_show[col].apply(lambda v: fmt_brl(v) if v else "—")

st.dataframe(df_show, use_container_width=True, height=400)

# STATUSBAR
st.markdown(f"""
<div style="padding:9px 0;font-size:11px;color:#AAA;display:flex;gap:22px;flex-wrap:wrap;margin-top:8px;">
  <span><strong style="color:#666">Arquivo:</strong> {st.session_state.arquivo or 'Nenhum carregado'}</span>
  <span><strong style="color:#666">Registros:</strong> {len(dados)}</span>
  <span><strong style="color:#666">Usuário:</strong> {user.get('nome') or user['login']}</span>
  <span><strong style="color:#666">Atualizado:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</span>
</div>
""", unsafe_allow_html=True)
